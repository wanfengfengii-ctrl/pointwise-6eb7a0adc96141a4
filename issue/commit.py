import sqlite3
from dataclasses import dataclass

from archive.database import StationError, station_transaction
from archive.index import insert_record_key
from archive.records import store_record_payload
from capture.manifest import check_text
from capture.parts import find_split
from issue.eligibility import check_eligibility, check_part_eligibility
from issue.order import advance_frontier
from issue.receipts import IssueReceipt, insert_receipt
from issue.release import release_if_settled
from process.results import StagedId


@dataclass(frozen=True)
class IssueResult:
    record_id: int
    receipt: IssueReceipt


def issue(db: sqlite3.Connection, batch_id: str, staged_id: StagedId) -> IssueResult:
    check_text(batch_id, 'INVALID_BATCH_ID')
    if not isinstance(staged_id, StagedId):
        raise StationError('INVALID_STAGED_ID')
    with station_transaction(db):
        candidate = check_eligibility(db, batch_id, staged_id)
        return _commit_candidate(db, candidate, advance_position=candidate.staged.result.frozen_id)


def issue_part(db: sqlite3.Connection, part_id: int, staged_id: StagedId) -> IssueResult:
    if not isinstance(part_id, int) or part_id <= 0:
        raise StationError('INVALID_PART_ID')
    if not isinstance(staged_id, StagedId):
        raise StationError('INVALID_STAGED_ID')
    with station_transaction(db):
        candidate = check_part_eligibility(db, part_id, staged_id)
        part = candidate.part
        assert part is not None
        parent_parts = find_split(db, part.parent_position)
        if parent_parts is None:
            raise StationError('BATCH_NOT_SPLIT', str(part.parent_position))
        back = parent_parts[1]
        # The frontier advances past the parent only when the back segment is signed.
        advance_position = part.parent_position if part.part_id == back.part_id else None
        return _commit_candidate(db, candidate, advance_position=advance_position)


def _commit_candidate(db, candidate, *, advance_position) -> IssueResult:
    part_id = candidate.part.part_id if candidate.part is not None else None
    key = insert_record_key(db, candidate.staged, part_id)
    store_record_payload(db, key, candidate.staged.result)
    receipt = insert_receipt(db, key.record_id, key.batch_id, key.staged_id, part_id)
    if advance_position is not None:
        advance_frontier(db, candidate.frontier, advance_position)
    release_if_settled(db, key.staged_id)
    return IssueResult(key.record_id, receipt)
