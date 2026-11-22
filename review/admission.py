import sqlite3
from dataclasses import dataclass

from archive.database import StationError, station_transaction
from process.results import StagedId, ThicknessResult
from process.slots import find_staged_result
from review.loans import REVIEW_SEAT, Loan, find_open_loan_for_seat, insert_loan


@dataclass(frozen=True)
class PreviewResult:
    loan: Loan
    result: ThicknessResult


def preview(db: sqlite3.Connection, seat: str, staged_id: StagedId) -> PreviewResult:
    if seat != REVIEW_SEAT:
        raise StationError('INVALID_SEAT')
    if not isinstance(staged_id, StagedId):
        raise StationError('INVALID_STAGED_ID')
    with station_transaction(db):
        if find_open_loan_for_seat(db, seat) is not None:
            raise StationError('SEAT_BUSY', seat)
        staged = find_staged_result(db, staged_id)
        if staged is None:
            raise StationError('STAGED_UNAVAILABLE', str(staged_id))
        loan = insert_loan(db, seat, staged.staged_id)
        return PreviewResult(loan, staged.result)
