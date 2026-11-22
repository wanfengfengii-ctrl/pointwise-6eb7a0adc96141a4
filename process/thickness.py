from capture.freeze import FrozenBatch
from process.results import ThicknessResult


def summarize_thickness(frozen: FrozenBatch) -> ThicknessResult:
    valid = [page.thickness_um for page in frozen.pages if page.thickness_um is not None]
    spread = max(valid) - min(valid) if valid else None
    return ThicknessResult(frozen.batch_id, frozen.frozen_id, frozen.pages, spread)
