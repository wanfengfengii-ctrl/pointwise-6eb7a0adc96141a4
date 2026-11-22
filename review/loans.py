import sqlite3
from dataclasses import dataclass

from archive.database import is_storage_id
from process.results import StagedId

REVIEW_SEAT = 'qc'


@dataclass(frozen=True)
class Loan:
    loan_id: int
    seat: str
    staged_id: StagedId
    closed: bool


def initialize_loan_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS review_loans (
            loan_id INTEGER PRIMARY KEY,
            seat TEXT NOT NULL CHECK (seat = 'qc'),
            slot INTEGER NOT NULL REFERENCES process_slots(slot),
            generation INTEGER NOT NULL CHECK (generation > 0),
            closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1))
        )
    ''')
    db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_loan_per_seat ON review_loans(seat)
        WHERE closed = 0
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS open_loan_by_result ON review_loans(slot, generation)
        WHERE closed = 0
    ''')


def _read_loan(row: tuple | None) -> Loan | None:
    if row is None:
        return None
    loan_id, seat, slot, generation, closed = row
    return Loan(loan_id, seat, StagedId(slot, generation), bool(closed))


def find_loan(db: sqlite3.Connection, loan_id: int) -> Loan | None:
    if not is_storage_id(loan_id):
        return None
    return _read_loan(db.execute('''
        SELECT loan_id, seat, slot, generation, closed FROM review_loans WHERE loan_id = ?
    ''', (loan_id,)).fetchone())


def find_open_loan_for_seat(db: sqlite3.Connection, seat: str) -> Loan | None:
    return _read_loan(db.execute('''
        SELECT loan_id, seat, slot, generation, closed FROM review_loans
        WHERE seat = ? AND closed = 0
    ''', (seat,)).fetchone())


def find_open_loan_for_result(db: sqlite3.Connection, staged_id: StagedId) -> Loan | None:
    return _read_loan(db.execute('''
        SELECT loan_id, seat, slot, generation, closed FROM review_loans
        WHERE slot = ? AND generation = ? AND closed = 0
    ''', (staged_id.slot, staged_id.generation)).fetchone())


def insert_loan(db: sqlite3.Connection, seat: str, staged_id: StagedId) -> Loan:
    inserted = db.execute('INSERT INTO review_loans(seat, slot, generation) VALUES (?, ?, ?)',
                          (seat, staged_id.slot, staged_id.generation))
    return Loan(inserted.lastrowid, seat, staged_id, closed=False)


def finish_loan(db: sqlite3.Connection, loan_id: int) -> bool:
    update = db.execute('UPDATE review_loans SET closed = 1 WHERE loan_id = ? AND closed = 0',
                        (loan_id,))
    return update.rowcount == 1
