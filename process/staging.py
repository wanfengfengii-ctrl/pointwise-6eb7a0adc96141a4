import sqlite3
from dataclasses import dataclass

from archive.database import StationError, station_transaction
from capture.batches import find_registration_at_position
from capture.freeze import load_frozen_batch
from capture.parts import find_split
from process.part_freeze import part_for_stage
from process.planning import SlotPlan, part_unit_key, plan_slot, whole_unit_key
from process.results import StagedId
from process.slots import write_slot
from process.thickness import summarize_part, summarize_thickness


@dataclass(frozen=True)
class StageResult:
    staged_id: StagedId
    reused: bool
    evicted_id: StagedId | None = None


def _commit_plan(db, plan: SlotPlan, result):
    previous = plan.expected.staged_id
    if plan.reuse:
        if previous is None:
            raise StationError('SLOT_INCONSISTENT', result.batch_id)
        return StageResult(previous, reused=True)
    staged_id = write_slot(db, plan.expected, result)
    return StageResult(staged_id, reused=False, evicted_id=previous)


def stage(db: sqlite3.Connection, frozen_id: int) -> StageResult:
    with station_transaction(db):
        if find_split(db, frozen_id) is not None:
            registration = find_registration_at_position(db, frozen_id)
            batch_id = registration.batch_id if registration is not None else str(frozen_id)
            raise StationError('BATCH_SPLIT', batch_id)
        frozen = load_frozen_batch(db, frozen_id)
        plan = plan_slot(db, whole_unit_key(frozen.frozen_id))
        result = summarize_thickness(frozen)
        return _commit_plan(db, plan, result)


def stage_part(db: sqlite3.Connection, part_id: int) -> StageResult:
    with station_transaction(db):
        part, frozen = part_for_stage(db, part_id)
        requester = part_unit_key(part.parent_position, part.ordinal, frozen.frozen_id)
        plan = plan_slot(db, requester)
        result = summarize_part(frozen, part.parent_position)
        return _commit_plan(db, plan, result)
