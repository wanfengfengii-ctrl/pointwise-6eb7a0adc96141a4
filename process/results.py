from dataclasses import dataclass

from archive.database import StationError, is_storage_id
from capture.pages import MeasuredPage


@dataclass(frozen=True)
class ThicknessResult:
    batch_id: str
    frozen_id: int
    pages: tuple[MeasuredPage, ...]
    range_um: int | None

    @property
    def missing_count(self) -> int:
        return sum(page.thickness_um is None for page in self.pages)


@dataclass(frozen=True)
class StagedId:
    slot: int
    generation: int

    def __post_init__(self) -> None:
        if not is_storage_id(self.slot) or not is_storage_id(self.generation):
            raise StationError('INVALID_STAGED_ID')


@dataclass(frozen=True)
class StagedResult:
    staged_id: StagedId
    result: ThicknessResult
