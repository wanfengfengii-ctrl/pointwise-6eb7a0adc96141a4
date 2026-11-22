import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.database import StationError, open_station_database
from archive.retrieve import get
from capture.batches import register
from capture.freeze import receive, split, split_status
from issue.commit import issue, issue_part
from process.slots import list_slots
from process.staging import stage, stage_part
from review.admission import preview
from review.closing import close
from review.readout import read_preview


SAMPLES = Path(__file__).resolve().parent / 'samples'


def read_sample():
    sample = json.loads((SAMPLES / 'shift.json').read_text(encoding='utf-8'))
    expected = json.loads((SAMPLES / 'expected.json').read_text(encoding='utf-8'))
    return sample, expected


def read_split_sample():
    return json.loads((SAMPLES / 'split_shift.json').read_text(encoding='utf-8'))


def _as_json(result):
    return {
        'part_id': getattr(result, 'part_frozen_id', None),
        'pages': [[page.page_id, page.thickness_um, page.source] for page in result.pages],
        'range_um': result.range_um,
        'missing_count': result.missing_count,
    }


def _require(condition, description):
    if not condition:
        raise AssertionError(description)


def run_split_demo(path, sample):
    arrivals = sample['arrivals']
    first_a, late_a = arrivals['A']
    (page_b,) = arrivals['B']
    expected = sample['expected_split']
    evidence = {}

    with closing(open_station_database(path)) as db:
        for batch, pages in sample['registrations']:
            register(db, batch, pages)

        # A1 arrives first; A is still short and cannot freeze.
        pending = receive(db, 'A', *first_a)
        _require(pending.frozen_id is None and pending.coverage.missing == ('2',),
                 'A must wait for page 2')

        # B is complete and staged into one of the two shared slots.
        b_frozen = receive(db, 'B', *page_b).frozen_id
        b_stage = stage(db, b_frozen).staged_id

        # Split A once, on the registered page order, after page 1.
        front_id, back_id = split(db, 'A', sample['after_page'])
        evidence['split'] = {'front_part_id': front_id, 'back_part_id': back_id}
        _require(split(db, 'A', sample['after_page']) == (front_id, back_id),
                 'same boundary replays the original segment ids')
        status = split_status(db, 'A')
        _require(status[0].missing == () and status[1].missing == ('2',),
                 'front complete, back still missing')
        _require(status[0].frozen_id is not None and status[1].frozen_id is None,
                 'only the front segment is frozen')

        # A1 re-delivered after the split is the same original receipt;
        # changing the value or the source must be refused.
        replay = receive(db, 'A', *first_a)
        _require(replay.replayed and replay.frozen_id is None,
                 'post-split A1 redelivery is recognized at the parent address')
        try:
            receive(db, 'A', first_a[0], first_a[1], first_a[2] + 1)
        except StationError as error:
            _require(error.code == 'PAGE_CONFLICT', 'changed A1 value must conflict')
        else:
            raise AssertionError('changed A1 value was accepted')

        # Front segment is processed, opened at the qc seat, then signed
        # while the preview borrow stays open (the slot stays occupied).
        front_stage = stage_part(db, front_id)
        loan_id = preview(db, 'qc', front_stage.staged_id).loan.loan_id
        front_issue = issue_part(db, front_id, front_stage.staged_id)
        evidence['front_issue'] = {'record_id': front_issue.record_id,
                                   'receipt': str(front_issue.receipt)}

        # B still cannot sign: the back segment of the head batch is pending.
        try:
            issue(db, 'B', b_stage)
        except StationError as error:
            _require(error.code == 'OUT_OF_ORDER', 'B remains behind the back segment')
        else:
            raise AssertionError('B was signed before the back segment')

        # The parent batch can never be issued as one whole batch.
        try:
            issue(db, 'A', front_stage.staged_id)
        except StationError as error:
            _require(error.code == 'BATCH_SPLIT', 'parent whole issue must be refused')
        else:
            raise AssertionError('split parent was issued as a whole')

        # A2 arrives at the original batch/page address; only the back freezes.
        late = receive(db, 'A', *late_a)
        _require(late.frozen_id is None, 'split parent never gets a whole freeze')
        late_status = split_status(db, 'A')
        _require(late_status[1].frozen_id is not None and late_status[1].missing == (),
                 'back segment freezes once A2 arrives')

        # Full-slot linkage: the front preview is still open and B holds the
        # other slot; the ready back segment reclaims B's unborrowed slot.
        back_stage = stage_part(db, back_id)
        _require(back_stage.evicted_id == b_stage,
                 'the back head unit must reclaim the unborrowed B staging')
        previewed_front = _as_json(read_preview(db, loan_id))
        expected_front_pages = [[first_a[0], first_a[2], first_a[1]]]
        _require(previewed_front['pages'] == expected_front_pages,
                 'the original A1 preview stays readable after reclaim')
        evidence['front_preview_after_reclaim'] = previewed_front

        back_issue = issue_part(db, back_id, back_stage.staged_id)
        # B recomputes from its original whole-batch frozen id, then signs.
        b_recomputed = stage(db, b_frozen)
        _require(b_recomputed.staged_id != b_stage, 'reclaimed B gets a new staged generation')
        b_issue = issue(db, 'B', b_recomputed.staged_id)
        close(db, loan_id)
        evidence['slot_generations'] = [
            {'slot': slot.slot, 'generation': slot.generation} for slot in list_slots(db)
        ]

    # Reopen the working library and collect the three formal archives in
    # handover order: A front segment, A back segment, then B.
    with closing(open_station_database(path)) as reopened:
        records = []
        for issued, part_id in (
            (front_issue, front_id),
            (back_issue, back_id),
            (b_issue, None),
        ):
            archived = get(reopened, issued.record_id)
            _require(archived.receipt == issued.receipt, 'first execution receipt is stable')
            _require(archived.part_id == part_id, 'part identity survives reopen')
            records.append({
                'record_id': archived.record_id,
                'part_id': archived.part_id,
                'batch_id': archived.result.batch_id,
                'frozen_id': archived.result.frozen_id,
                'part_frozen_id': archived.result.part_frozen_id,
                'pages': [[page.page_id, page.thickness_um, page.source]
                          for page in archived.result.pages],
                'range_um': archived.result.range_um,
                'missing_count': archived.result.missing_count,
                'receipt': str(archived.receipt),
            })

    for actual, want in zip(records, expected, strict=True):
        _require(actual['batch_id'] == want['batch_id'], 'archive batch order')
        _require(actual['pages'] == want['pages'], f"pages for {want['batch_id']} {want['segment']}")
        _require(actual['range_um'] == want['range_um'], 'hand-calculated range')
        _require(actual['missing_count'] == want['missing_count'], 'missing count')
    evidence['archives'] = records
    return evidence


def run_demo(path, sample, expected):
    arrivals = sample['arrivals']
    with closing(open_station_database(path)) as db:
        for batch, manifest in sample['registrations'].items():
            register(db, batch, manifest)
        pending = receive(db, 'A', *arrivals['A'][0])
        assert pending.coverage.missing == ('2',)
        receive(db, 'B', *arrivals['B'][0])
        b = receive(db, 'B', *arrivals['B'][1])
        b_stage = stage(db, b.frozen_id).staged_id
        b_preview = preview(db, 'qc', b_stage)
        loan = b_preview.loan.loan_id
        receive(db, 'C', *arrivals['C'][0])
        c = receive(db, 'C', *arrivals['C'][1])
        try:
            c_first = stage(db, c.frozen_id).staged_id
        except StationError as error:
            if error.code != 'NO_SLOT':
                raise
            c_first = None
        a = receive(db, 'A', *arrivals['A'][1])
        a_stage = stage(db, a.frozen_id)
        if c_first is not None:
            assert a_stage.evicted_id == c_first
        b_during = _as_json(read_preview(db, loan))

        a_issued = issue(db, 'A', a_stage.staged_id)
        b_issued = issue(db, 'B', b_stage)
        b_after_issue = _as_json(read_preview(db, loan))
        assert close(db, loan).slot_released
        c_stage = stage(db, c.frozen_id).staged_id
        c_preview = preview(db, 'qc', c_stage)
        assert close(db, loan).closed_now is False
        c_after_replay = _as_json(read_preview(db, c_preview.loan.loan_id))
        c_issued = issue(db, 'C', c_stage)
        close(db, c_preview.loan.loan_id)

    actual = {
        'B_preliminary': _as_json(b_preview.result),
        'B_during_A_staging': b_during,
        'B_after_issue': b_after_issue,
        'C_after_old_close': c_after_replay,
    }
    preview_expected = {
        'B_preliminary': expected['B'],
        'B_during_A_staging': expected['B'],
        'B_after_issue': expected['B'],
        'C_after_old_close': expected['C'],
    }
    downstream = {}
    record_ids = []
    with closing(open_station_database(path)) as db:
        for batch, issued in zip(('A', 'B', 'C'), (a_issued, b_issued, c_issued), strict=True):
            archived = get(db, issued.record_id)
            assert archived.result.batch_id == batch
            assert archived.receipt == issued.receipt
            record_ids.append(archived.record_id)
            downstream[batch] = _as_json(archived.result)
    assert record_ids == sorted(set(record_ids))
    assert actual == preview_expected
    assert downstream == expected
    return {
        'case_id': 'v5',
        'input': sample,
        'expected': preview_expected,
        'actual': actual,
        'downstream_expected': expected,
        'downstream_actual': downstream,
    }


def main():
    sample = read_split_sample()
    with TemporaryDirectory(prefix='beiting-split-demo-') as directory:
        evidence = run_split_demo(Path(directory) / 'station.sqlite', sample)
    print('POINTWISE_SPLIT_SHIFT ' + json.dumps(
        {'case_id': 'split-v1', 'input': sample, 'evidence': evidence},
        ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
