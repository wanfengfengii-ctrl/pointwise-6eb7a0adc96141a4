import sqlite3
from dataclasses import dataclass

from process.results import StagedId


@dataclass(frozen=True)
class IssueReceipt:
    receipt_id: int
    record_id: int
    batch_id: str
    staged_id: StagedId


def initialize_receipt_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS issue_receipts (
            receipt_id INTEGER PRIMARY KEY,
            record_id INTEGER NOT NULL UNIQUE REFERENCES archive_index(record_id),
            batch_id TEXT NOT NULL UNIQUE REFERENCES registrations(batch_id),
            slot INTEGER NOT NULL REFERENCES process_slots(slot),
            generation INTEGER NOT NULL CHECK (generation > 0),
            UNIQUE (slot, generation)
        )
    ''')


def insert_receipt(db: sqlite3.Connection, record_id: int, batch_id: str,
                   staged_id: StagedId) -> IssueReceipt:
    inserted = db.execute('''
        INSERT INTO issue_receipts(record_id, batch_id, slot, generation) VALUES (?, ?, ?, ?)
    ''', (record_id, batch_id, staged_id.slot, staged_id.generation))
    return IssueReceipt(inserted.lastrowid, record_id, batch_id, staged_id)


def _read_receipt(row: tuple | None) -> IssueReceipt | None:
    if row is None:
        return None
    receipt_id, record_id, batch_id, slot, generation = row
    return IssueReceipt(receipt_id, record_id, batch_id, StagedId(slot, generation))


def receipt_for_record(db: sqlite3.Connection, record_id: int) -> IssueReceipt | None:
    return _read_receipt(db.execute('''
        SELECT receipt_id, record_id, batch_id, slot, generation FROM issue_receipts
        WHERE record_id = ?
    ''', (record_id,)).fetchone())


def receipt_for_staged_result(db: sqlite3.Connection, staged_id: StagedId) -> IssueReceipt | None:
    return _read_receipt(db.execute('''
        SELECT receipt_id, record_id, batch_id, slot, generation FROM issue_receipts
        WHERE slot = ? AND generation = ?
    ''', (staged_id.slot, staged_id.generation)).fetchone())
