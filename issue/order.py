import sqlite3

from archive.database import StationError
from capture.batches import BatchRegistration, first_registration_after


def initialize_issue_order(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS issue_order (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            issued_through INTEGER NOT NULL CHECK (issued_through >= 0)
        )
    ''')
    db.execute('INSERT OR IGNORE INTO issue_order(singleton, issued_through) VALUES (1, 0)')


def current_frontier(db: sqlite3.Connection) -> int:
    return db.execute('SELECT issued_through FROM issue_order WHERE singleton = 1').fetchone()[0]


def next_unissued_batch(db: sqlite3.Connection, frontier: int | None = None) -> BatchRegistration | None:
    if frontier is None:
        frontier = current_frontier(db)
    return first_registration_after(db, frontier)


def advance_frontier(db: sqlite3.Connection, previous: int, position: int) -> None:
    update = db.execute('''
        UPDATE issue_order SET issued_through = ?
        WHERE singleton = 1 AND issued_through = ? AND issued_through < ?
    ''', (position, previous, position))
    if update.rowcount != 1:
        raise StationError('FRONTIER_CHANGED', str(previous))
