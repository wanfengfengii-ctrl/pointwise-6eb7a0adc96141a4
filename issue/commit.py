import sqlite3
from dataclasses import dataclass

from archive.database import StationError, station_transaction
from archive.index import insert_record_key
from archive.records import store_record_payload
from capture.manifest import check_text
from issue.eligibility import check_eligibility
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
        key = insert_record_key(db, candidate.staged)
        store_record_payload(db, key, candidate.staged.result)
        receipt = insert_receipt(db, key.record_id, key.batch_id, key.staged_id)
        advance_frontier(db, candidate.frontier, key.frozen_id)
        release_if_settled(db, key.staged_id)
    return IssueResult(key.record_id, receipt)
