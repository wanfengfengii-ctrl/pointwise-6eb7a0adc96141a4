import sqlite3
from dataclasses import dataclass

from archive.database import StationError
from capture.batches import find_registration
from capture.freeze import find_frozen_batch
from issue.order import current_frontier, next_unissued_batch
from process.results import StagedId, StagedResult
from process.slots import find_staged_result


@dataclass(frozen=True)
class IssueCandidate:
    frontier: int
    staged: StagedResult


def check_eligibility(db: sqlite3.Connection, batch_id: str, staged_id: StagedId) -> IssueCandidate:
    registration = find_registration(db, batch_id)
    if registration is None:
        raise StationError('BATCH_NOT_FOUND', batch_id)
    frontier = current_frontier(db)
    if registration.position <= frontier:
        raise StationError('ALREADY_ISSUED', batch_id)
    head = next_unissued_batch(db, frontier)
    if head is None or head.position != registration.position:
        raise StationError('OUT_OF_ORDER', batch_id)
    frozen = find_frozen_batch(db, batch_id)
    if frozen is None:
        raise StationError('BATCH_NOT_READY', batch_id)
    staged = find_staged_result(db, staged_id)
    if staged is None:
        raise StationError('STAGED_UNAVAILABLE', str(staged_id))
    if staged.result.frozen_id != frozen.frozen_id or staged.result.batch_id != batch_id:
        raise StationError('STAGE_BATCH_MISMATCH', batch_id)
    return IssueCandidate(frontier, staged)
