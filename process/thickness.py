from capture.freeze import FrozenBatch
from process.part_freeze import FrozenPart
from process.results import ThicknessResult


def _spread(pages):
    valid = [page.thickness_um for page in pages if page.thickness_um is not None]
    return max(valid) - min(valid) if valid else None


def summarize_thickness(frozen: FrozenBatch) -> ThicknessResult:
    return ThicknessResult(frozen.batch_id, frozen.frozen_id, frozen.pages, _spread(frozen.pages))


def summarize_part(frozen: FrozenPart, parent_frozen_id: int) -> ThicknessResult:
    return ThicknessResult(
        frozen.batch_id,
        parent_frozen_id,
        frozen.pages,
        _spread(frozen.pages),
        part_frozen_id=frozen.frozen_id,
    )
