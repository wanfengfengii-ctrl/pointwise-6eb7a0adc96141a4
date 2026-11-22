import sqlite3
from dataclasses import dataclass

from archive.database import StationError
from capture.freeze import FrozenBatch
from issue.order import current_frontier, next_unissued_batch
from process.slots import SlotOccupancy, list_slots
from review.loans import find_open_loan_for_result


@dataclass(frozen=True)
class SlotPlan:
    expected: SlotOccupancy
    reuse: bool = False


def plan_stage(db: sqlite3.Connection, frozen: FrozenBatch) -> SlotPlan:
    frontier = current_frontier(db)
    if frozen.frozen_id <= frontier:
        raise StationError('ALREADY_ISSUED', frozen.batch_id)

    slots = list_slots(db)
    for occupancy in slots:
        if occupancy.frozen_id == frozen.frozen_id:
            return SlotPlan(occupancy, reuse=True)
    for occupancy in slots:
        if occupancy.frozen_id is None:
            return SlotPlan(occupancy)

    head = next_unissued_batch(db, frontier)
    if head is None or head.position != frozen.frozen_id:
        raise StationError('NO_SLOT', frozen.batch_id)

    # A signed occupant can remain only while borrowed. There is one seat,
    # so at least one of the two full slots is an unborrowed later batch.
    for occupancy in reversed(slots):
        staged_id = occupancy.staged_id
        if (staged_id is not None and occupancy.frozen_id > head.position
                and find_open_loan_for_result(db, staged_id) is None):
            return SlotPlan(occupancy)
    raise StationError('EARLY_BATCH_NO_SLOT', frozen.batch_id)
