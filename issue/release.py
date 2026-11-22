import sqlite3

from issue.receipts import receipt_for_staged_result
from process.results import StagedId
from process.slots import clear_slot
from review.loans import find_open_loan_for_result


def release_if_settled(db: sqlite3.Connection, staged_id: StagedId) -> bool:
    if receipt_for_staged_result(db, staged_id) is None:
        return False
    if find_open_loan_for_result(db, staged_id) is not None:
        return False
    return clear_slot(db, staged_id)
