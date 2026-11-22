import sqlite3
from dataclasses import dataclass

from archive.audit import check_record_binding
from archive.database import StationError, station_transaction
from archive.index import locate_record
from archive.records import load_record_payload
from issue.receipts import IssueReceipt, receipt_for_record
from process.results import ThicknessResult


@dataclass(frozen=True)
class RetrievedRecord:
    record_id: int
    result: ThicknessResult
    receipt: IssueReceipt
    part_id: int | None = None


def get(db: sqlite3.Connection, record_id: int) -> RetrievedRecord:
    with station_transaction(db, write=False):
        key = locate_record(db, record_id)
        if key is None:
            raise StationError('RECORD_NOT_FOUND')
        result = load_record_payload(db, key)
        receipt = receipt_for_record(db, key.record_id)
        if result is None or receipt is None:
            raise StationError('ARCHIVE_INCOMPLETE', str(key.record_id))
        check_record_binding(key, result, receipt)
        return RetrievedRecord(key.record_id, result, receipt, key.part_id)
