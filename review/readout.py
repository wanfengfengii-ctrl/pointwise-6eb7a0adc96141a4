import sqlite3

from archive.database import StationError, station_transaction
from process.results import ThicknessResult
from process.slots import find_staged_result
from review.loans import find_loan


def read_preview(db: sqlite3.Connection, loan_id: int) -> ThicknessResult:
    with station_transaction(db, write=False):
        loan = find_loan(db, loan_id)
        if loan is None:
            raise StationError('LOAN_NOT_FOUND')
        if loan.closed:
            raise StationError('LOAN_CLOSED', str(loan_id))
        staged = find_staged_result(db, loan.staged_id)
        if staged is None:
            raise StationError('PREVIEW_RESULT_LOST', str(loan_id))
        return staged.result
