import sqlite3
from dataclasses import dataclass

from archive.database import StationError
from capture.parts import find_split, part_is_issued
from issue.order import current_frontier, next_unissued_batch
from process.part_freeze import frozen_part_for_id
from process.slots import SlotOccupancy, list_slots
from review.loans import find_open_loan_for_result

PART_ORDINAL_SCALE = 10.0


@dataclass(frozen=True)
class UnitKey:
    """Ordering identity of a stageable unit within the registration queue.

    Whole batches use their registration position. Split parts use their
    parent position with a fractional ordinal (front before back).
    """

    position: float
    frozen_id: int
    part_frozen_id: int | None = None

    @property
    def is_part(self) -> bool:
        return self.part_frozen_id is not None


def whole_unit_key(frozen_id: int) -> UnitKey:
    return UnitKey(float(frozen_id), frozen_id)


def part_unit_key(parent_position: int, ordinal: int, part_frozen_id: int) -> UnitKey:
    return UnitKey(parent_position + ordinal / PART_ORDINAL_SCALE,
                   parent_position, part_frozen_id)


def occupant_key(db: sqlite3.Connection, occupancy: SlotOccupancy) -> UnitKey | None:
    if occupancy.frozen_id is not None:
        return whole_unit_key(occupancy.frozen_id)
    if occupancy.part_frozen_id is not None:
        part = _part_for_part_frozen(db, occupancy.part_frozen_id)
        return part_unit_key(part.parent_position, part.ordinal, occupancy.part_frozen_id)
    return None


def _part_for_part_frozen(db: sqlite3.Connection, part_frozen_id: int):
    row = db.execute('''
        SELECT p.part_id, p.parent_position, p.ordinal, p.after_page
        FROM part_frozen_batches AS f
        JOIN batch_parts AS p ON p.part_id = f.part_id
        WHERE f.frozen_id = ?
    ''', (part_frozen_id,)).fetchone()
    if row is None:
        raise StationError('SLOT_INCONSISTENT', str(part_frozen_id))
    from capture.parts import BatchPart
    return BatchPart(*row)


def head_unit(db: sqlite3.Connection) -> UnitKey | None:
    """The current first signable unit: head batch, split into its parts."""
    frontier = current_frontier(db)
    head = next_unissued_batch(db, frontier)
    if head is None:
        return None
    parts = find_split(db, head.position)
    if parts is None:
        return whole_unit_key(head.position)
    for part in parts:
        if part_is_issued(db, part.part_id):
            continue
        frozen = frozen_part_for_id(db, part.part_id)
        if frozen is None:
            return None
        return part_unit_key(head.position, part.ordinal, frozen.frozen_id)
    # Both parts archived: the parent is fully handed over.
    return None


@dataclass(frozen=True)
class SlotPlan:
    expected: SlotOccupancy
    reuse: bool = False


def plan_slot(db: sqlite3.Connection, requester: UnitKey) -> SlotPlan:
    slots = list_slots(db)
    for occupancy in slots:
        key = occupant_key(db, occupancy)
        if key is not None and key == requester:
            return SlotPlan(occupancy, reuse=True)
    for occupancy in slots:
        if occupant_key(db, occupancy) is None:
            return SlotPlan(occupancy)

    head = head_unit(db)
    if head is None or head != requester:
        raise StationError('NO_SLOT', str(requester.frozen_id))

    # A signed occupant can remain only while borrowed. There is one seat,
    # so at least one of the two full slots is an unborrowed later unit.
    for occupancy in reversed(slots):
        staged_id = occupancy.staged_id
        key = occupant_key(db, occupancy)
        if (staged_id is not None and key is not None
                and key.position > requester.position
                and find_open_loan_for_result(db, staged_id) is None):
            return SlotPlan(occupancy)
    raise StationError('EARLY_BATCH_NO_SLOT', str(requester.frozen_id))
