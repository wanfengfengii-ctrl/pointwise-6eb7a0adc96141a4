import sqlite3
from dataclasses import dataclass

from archive.database import StationError, station_transaction
from issue.release import release_if_settled
from review.loans import find_loan, finish_loan


@dataclass(frozen=True)
class CloseResult:
    loan_id: int
    closed_now: bool
    slot_released: bool


def close(db: sqlite3.Connection, loan_id: int) -> CloseResult:
    with station_transaction(db):
        loan = find_loan(db, loan_id)
        if loan is None:
            raise StationError('LOAN_NOT_FOUND')
        if loan.closed:
            return CloseResult(loan_id, closed_now=False, slot_released=False)
        if not finish_loan(db, loan.loan_id):
            raise StationError('LOAN_CHANGED', str(loan.loan_id))
        released = release_if_settled(db, loan.staged_id)
        return CloseResult(loan.loan_id, closed_now=True, slot_released=released)
