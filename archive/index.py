import sqlite3
from dataclasses import dataclass

from archive.database import is_storage_id
from archive.migration import legacy_table_rebuild, table_has_column
from process.results import StagedId, StagedResult


@dataclass(frozen=True)
class RecordKey:
    record_id: int
    batch_id: str
    frozen_id: int
    staged_id: StagedId
    part_id: int | None = None


def initialize_record_index(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS archive_index (
            record_id INTEGER PRIMARY KEY,
            batch_id TEXT NOT NULL REFERENCES registrations(batch_id),
            frozen_id INTEGER NOT NULL,
            part_id INTEGER UNIQUE REFERENCES batch_parts(part_id),
            slot INTEGER NOT NULL REFERENCES process_slots(slot),
            generation INTEGER NOT NULL CHECK (generation > 0),
            UNIQUE (slot, generation),
            UNIQUE (part_id),
            UNIQUE (frozen_id, part_id)
        )
    ''')
    _migrate_legacy_index(db)
    db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS archive_whole_batch_once
        ON archive_index(batch_id) WHERE part_id IS NULL
    ''')


def _migrate_legacy_index(db: sqlite3.Connection) -> None:
    if table_has_column(db, 'archive_index', 'part_id'):
        return
    with legacy_table_rebuild(db):
        db.execute('ALTER TABLE archive_index RENAME TO archive_index_legacy')
        db.execute('''
            CREATE TABLE archive_index (
                record_id INTEGER PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES registrations(batch_id),
                frozen_id INTEGER NOT NULL,
                part_id INTEGER UNIQUE REFERENCES batch_parts(part_id),
                slot INTEGER NOT NULL REFERENCES process_slots(slot),
                generation INTEGER NOT NULL CHECK (generation > 0),
                UNIQUE (slot, generation),
                UNIQUE (part_id),
                UNIQUE (frozen_id, part_id)
            )
        ''')
        db.execute('''
            INSERT INTO archive_index(record_id, batch_id, frozen_id, part_id, slot, generation)
            SELECT record_id, batch_id, frozen_id, NULL, slot, generation FROM archive_index_legacy
        ''')
        # archive_records keeps a foreign key to the renamed table after the
        # rename; rebuild it so its reference targets the new archive_index.
        db.execute('ALTER TABLE archive_records RENAME TO archive_records_legacy')
        db.execute('''
            CREATE TABLE archive_records (
                record_id INTEGER PRIMARY KEY REFERENCES archive_index(record_id),
                payload TEXT NOT NULL
            )
        ''')
        db.execute('''
            INSERT INTO archive_records(record_id, payload)
            SELECT record_id, payload FROM archive_records_legacy
        ''')
        db.execute('DROP TABLE archive_records_legacy')
        db.execute('DROP TABLE archive_index_legacy')


def insert_record_key(db: sqlite3.Connection, staged: StagedResult,
                      part_id: int | None = None) -> RecordKey:
    result = staged.result
    staged_id = staged.staged_id
    inserted = db.execute('''
        INSERT INTO archive_index(batch_id, frozen_id, part_id, slot, generation)
        VALUES (?, ?, ?, ?, ?)
    ''', (result.batch_id, result.frozen_id, part_id, staged_id.slot, staged_id.generation))
    return RecordKey(inserted.lastrowid, result.batch_id, result.frozen_id,
                     staged_id, part_id)


def locate_record(db: sqlite3.Connection, record_id: int) -> RecordKey | None:
    if not is_storage_id(record_id):
        return None
    row = db.execute('''
        SELECT record_id, batch_id, frozen_id, part_id, slot, generation
        FROM archive_index WHERE record_id = ?
    ''', (record_id,)).fetchone()
    if row is None:
        return None
    found_id, batch_id, frozen_id, part_id, slot, generation = row
    return RecordKey(found_id, batch_id, frozen_id, StagedId(slot, generation), part_id)
