# 北汀带材质检站

线扫工位上传带材厚度测页。夜班质检员在早批等待补页时可以先复核晚批带面，白班按批次登记序取得正式成果及首执。Python 3.11 标准库和单个 SQLite 工作库即可运行。

## 运行

在项目根目录运行 `python3 -B check_project.py` 执行完整回归。`check_project.py` 由运行平台提供。独立运行 `python3 -B demo.py` 复现工作剧本。演示用新建的临时工作库，签发后重开原文件领取。样例输入在 `samples/shift.json`，独立手算预期在 `samples/expected.json`。

## 一次交班

1. 按 A、B、C 登记批次和应报页序。线扫工位先上传 A 的第 1 页，读数为 9 微米。
2. B 第 2 页的 11 微米先到，第 1 页的 7 微米后到。齐页时冻结 B 的原序清单。
3. 夜班加工 B 并打开预核，按原序复查 7、11 微米和逐页出处。此时 B 尚未正式签发。
4. C 齐页后请求暂存。C 的 20、24 微米也给出极差 4 微米，各自保留自己的原据。
5. A 的第 2 页以具名缺测页实收。A 立即取得下一次加工机会，满槽时可收回无人借阅的晚批成果。B 原预核保持可读。
6. 白班先签 A、再签 B。B 的正式档案可领，夜班仍可沿原预核号续读 B。
7. 夜班关闭 B，C 凭原冻结页加工并可复用 B 的物理槽。再次提交 B 原关闭请求后，C 仍可复查和签发。
8. 重开工作库，白班按各自成果号领取 A、B、C 的原序成果和首执。

## Python 接口

所有入口的第一个参数是 `archive.database.open_station_database(path)` 返回的连接。函数拥有自己的短事务，调用方负责关闭连接。不同调用方可以各开一个连接。`initialize_station(db)` 可重复执行，保留已登记、实收、冻结、暂存、预核、编号进度和已签档案。工作库首次建立时设置两个固定槽及空签发前沿。初始化只建立必要结构。

| 模块及入口 | 输入与成功结果 |
| --- | --- |
| `capture.batches.register(db, batch_id, pages)` | 批号及有序应报页序列，返回 `BatchRegistration`，含登记位置、批号及清单。 |
| `capture.freeze.receive(db, batch_id, page_id, source, thickness_um)` | 具名测页、出处及整数微米或 `None`，返回 `ReceiveResult`，含原序已收和欠页集合及齐页后的 `frozen_id`。 |
| `capture.freeze.split(db, batch_id, after_page)` | 仅对最早未签且仍欠页的批，在登记页序 `after_page` 之后二分一次，返回 `(前段号, 后段号)`。同界重投领原段号，异界或不合资格报 `StationError`。 |
| `capture.freeze.split_status(db, batch_id)` | 返回前、后两个 `PartStatus`（`part_id`、按登记顺序的 `missing`、齐页后固定的 `frozen_id`）。 |
| `process.staging.stage(db, frozen_id)` | 加工已冻结批，返回 `StageResult`，含具体 `staged_id`、是否复用现有成果及本次收回的旧成果号（如有）。分段父批改走 `stage_part`。 |
| `process.staging.stage_part(db, part_id)` | 加工已齐页且未签的段，沿用 `StageResult`，统计按本段原序测页计算，与普通批共用两个槽。 |
| `review.admission.preview(db, seat, staged_id)` | 以席名 `qc` 打开预核，返回 `PreviewResult`，含借阅号和固定成果。 |
| `review.readout.read_preview(db, loan_id)` | 续读仍开放的原预核，返回原 `ThicknessResult`。 |
| `review.closing.close(db, loan_id)` | 结清原借阅，返回 `CloseResult`，含是否由本次调用关闭及是否释放槽。 |
| `issue.commit.issue(db, batch_id, staged_id)` | 按顺位签存指定批和它的具体暂存成果，返回 `IssueResult`，含固定 `record_id` 和首执。分段父批不能经此入口签整件。 |
| `issue.commit.issue_part(db, part_id, staged_id)` | 签发指定段，前段可在后段欠页时签出，后段仍排在后续批之前；返回 `IssueResult`。 |
| `archive.retrieve.get(db, record_id)` | 领取 `RetrievedRecord`，含固定成果号、原成果、原首执及分段档案的 `.part_id`（未分段批为 `None`）。 |

批号、页号和出处是非空 UTF-8 文本，保持原拼写。应报清单至少一页且页号唯一。具名缺测页也须提供出处，算作实收，结果保留该页位置。数值必须是 Python `int`，`None` 表示缺测。极差是有效值的最大值减最小值，一个有效值为 0、全缺测为空。缺测数由原序测页计数。读数和极差全程保持整数精度。

登记和实收的精确重投复用原身份，冲突返回 `REGISTRATION_CONFLICT` 或 `PAGE_CONFLICT`。无效清单、读数、出处及清单外页在实收或登记边界拒绝。欠页返回 `BATCH_NOT_READY`。晚批暂时没有槽时返回 `NO_SLOT`，可按原冻结号重试。被收回的具体暂存号无法开借或签发。席位已有借阅时报 `SEAT_BUSY`，未知和已关闭预核分别报 `LOAN_NOT_FOUND` 与 `LOAN_CLOSED`。重投已关闭号返回 `closed_now=False`。越序签发报 `OUT_OF_ORDER`，使用其他批的暂存成果报 `STAGE_BATCH_MISMATCH`，已经签发的批报 `ALREADY_ISSUED`。正式档案未知号报 `RECORD_NOT_FOUND`。业务错误是带 `code` 属性的 `archive.database.StationError`；存储错误由 SQLite 抛出，事务回滚。

分段交班另用以下错误码：不具分段资格或已齐页报 `BATCH_NOT_READY`/`OUT_OF_ORDER`，末页划界报 `INVALID_SPLIT_BOUNDARY`，清单外边界报 `FOREIGN_PAGE`，已分段批另选边界报 `SPLIT_BOUNDARY_CONFLICT`，查询未分段批报 `BATCH_NOT_SPLIT`，对分段父批走整件入口报 `BATCH_SPLIT`，未知段号和未齐页段分别报 `PART_NOT_FOUND` 与 `PART_NOT_READY`。

## 欠页批分段交班

欠页的最早未签批可沿登记页序二分一次（`SPLIT.md` 为完整契约）。线扫工位继续以原批号、原页号交页，原址重投按原值和出处核对，具名缺测页仍计实收；切分前后的页按登记边界分属两段，各自齐页后独立冻结并取独立段冻结号。分段父批的 `receive` 始终返回 `frozen_id=None`，也不再产生整件冻结或签发。

前段齐页即可加工、预核和签出，后段欠页不阻塞前段；但后段未签前，后续批的普通 `issue` 仍报 `OUT_OF_ORDER`。两段与普通批共用两个物理槽，齐页待签的队首单元在满槽时可收回一个未签且无人借阅的后续暂存，被收回的暂存号失效、可凭原冻结号重新加工。开放中的预核始终读原槽代次。每段各自持有成果号、首执和签发顺位，档案、首执、顺位与当次槽结算同事务提交，失败不留半账。重开工作库后凭成果号领回父批号、段号、段冻结号、原序测页、出处、整数微米极差、缺测数及首执；未分段批的旧档和返回形状保持不变，旧工作库打开时自动迁移表结构并保留全部原数据。

槽成果由物理槽号、写入代次和冻结批绑定。空槽优先取槽号较小者，最早未签批必要时收回一份无人借阅的晚批成果。在读成果签发后继续占原槽，普通未签预核关闭时成果继续待签。签存档案、首执、前沿推进和当次槽释放同事务提交。领取从正式档案读取完整结果，核对首执与批次和具体暂存身份的关联。
