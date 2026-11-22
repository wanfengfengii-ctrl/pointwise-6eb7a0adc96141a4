import sqlite3
from dataclasses import dataclass

from archive.database import is_storage_id
from process.results import StagedId, StagedResult


@dataclass(frozen=True)
class RecordKey:
    record_id: int
    batch_id: str
    frozen_id: int
    staged_id: StagedId


def initialize_record_index(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS archive_index (
            record_id INTEGER PRIMARY KEY,
            batch_id TEXT NOT NULL UNIQUE REFERENCES registrations(batch_id),
            frozen_id INTEGER NOT NULL UNIQUE REFERENCES frozen_batches(frozen_id),
            slot INTEGER NOT NULL REFERENCES process_slots(slot),
            generation INTEGER NOT NULL CHECK (generation > 0),
            UNIQUE (slot, generation)
        )
    ''')


def insert_record_key(db: sqlite3.Connection, staged: StagedResult) -> RecordKey:
    result = staged.result
    staged_id = staged.staged_id
    inserted = db.execute('''
        INSERT INTO archive_index(batch_id, frozen_id, slot, generation) VALUES (?, ?, ?, ?)
    ''', (result.batch_id, result.frozen_id, staged_id.slot, staged_id.generation))
    return RecordKey(inserted.lastrowid, result.batch_id, result.frozen_id, staged_id)


def locate_record(db: sqlite3.Connection, record_id: int) -> RecordKey | None:
    if not is_storage_id(record_id):
        return None
    row = db.execute('''
        SELECT record_id, batch_id, frozen_id, slot, generation FROM archive_index WHERE record_id = ?
    ''', (record_id,)).fetchone()
    if row is None:
        return None
    found_id, batch_id, frozen_id, slot, generation = row
    return RecordKey(found_id, batch_id, frozen_id, StagedId(slot, generation))
