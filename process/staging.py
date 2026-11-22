import sqlite3
from dataclasses import dataclass

from archive.database import StationError, station_transaction
from capture.freeze import load_frozen_batch
from process.planning import plan_stage
from process.results import StagedId
from process.slots import write_slot
from process.thickness import summarize_thickness


@dataclass(frozen=True)
class StageResult:
    staged_id: StagedId
    reused: bool
    evicted_id: StagedId | None = None


def stage(db: sqlite3.Connection, frozen_id: int) -> StageResult:
    with station_transaction(db):
        frozen = load_frozen_batch(db, frozen_id)
        plan = plan_stage(db, frozen)
        previous = plan.expected.staged_id
        if plan.reuse:
            if previous is None:
                raise StationError('SLOT_INCONSISTENT', frozen.batch_id)
            return StageResult(previous, reused=True)
        result = summarize_thickness(frozen)
        staged_id = write_slot(db, plan.expected, result)
        return StageResult(staged_id, reused=False, evicted_id=previous)
