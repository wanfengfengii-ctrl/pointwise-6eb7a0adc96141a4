import sqlite3
from dataclasses import dataclass

from archive.database import StationError
from capture.batches import find_registration
from capture.freeze import find_frozen_batch
from capture.parts import BatchPart, find_batch_part, part_is_issued
from issue.order import current_frontier, next_unissued_batch
from process.part_freeze import frozen_part_for_id
from process.planning import head_unit, part_unit_key
from process.results import StagedId, StagedResult
from process.slots import find_staged_result


@dataclass(frozen=True)
class IssueCandidate:
    frontier: int
    staged: StagedResult
    part: BatchPart | None = None


def check_eligibility(db: sqlite3.Connection, batch_id: str, staged_id: StagedId) -> IssueCandidate:
    registration = find_registration(db, batch_id)
    if registration is None:
        raise StationError('BATCH_NOT_FOUND', batch_id)
    if _split_parts(db, registration) is not None:
        # A split parent can never be issued as one whole batch.
        raise StationError('BATCH_SPLIT', batch_id)
    frontier = current_frontier(db)
    if registration.position <= frontier:
        raise StationError('ALREADY_ISSUED', batch_id)
    head = next_unissued_batch(db, frontier)
    if head is None or head.position != registration.position:
        raise StationError('OUT_OF_ORDER', batch_id)
    frozen = find_frozen_batch(db, batch_id)
    if frozen is None:
        raise StationError('BATCH_NOT_READY', batch_id)
    staged = _require_matching_staged(db, staged_id, frozen.frozen_id, batch_id, None)
    return IssueCandidate(frontier, staged)


def check_part_eligibility(db: sqlite3.Connection, part_id: int,
                           staged_id: StagedId) -> IssueCandidate:
    part = find_batch_part(db, part_id)
    if part is None:
        raise StationError('PART_NOT_FOUND', str(part_id))
    if part_is_issued(db, part_id):
        raise StationError('ALREADY_ISSUED', str(part_id))
    frozen = frozen_part_for_id(db, part_id)
    if frozen is None:
        raise StationError('PART_NOT_READY', str(part_id))
    head = head_unit(db)
    requester = part_unit_key(part.parent_position, part.ordinal, frozen.frozen_id)
    if head is None or head != requester:
        raise StationError('OUT_OF_ORDER', str(part_id))
    staged = _require_matching_staged(db, staged_id, frozen.frozen_id, frozen.batch_id, part)
    frontier = current_frontier(db)
    return IssueCandidate(frontier, staged, part)


def _split_parts(db, registration):
    from capture.parts import find_split
    return find_split(db, registration.position)


def _require_matching_staged(db, staged_id: StagedId, expected_frozen_id: int,
                             batch_id: str, part: BatchPart | None) -> StagedResult:
    if not isinstance(staged_id, StagedId):
        raise StationError('INVALID_STAGED_ID')
    staged = find_staged_result(db, staged_id)
    if staged is None:
        raise StationError('STAGED_UNAVAILABLE', str(staged_id))
    result = staged.result
    if result.batch_id != batch_id:
        raise StationError('STAGE_BATCH_MISMATCH', batch_id)
    if part is None:
        if result.part_frozen_id is not None or result.frozen_id != expected_frozen_id:
            raise StationError('STAGE_BATCH_MISMATCH', batch_id)
    else:
        if (result.part_frozen_id != expected_frozen_id
                or result.frozen_id != part.parent_position):
            raise StationError('STAGE_BATCH_MISMATCH', batch_id)
    return staged
