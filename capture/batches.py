import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from archive.database import StationError, station_transaction
from capture.manifest import ExpectedPages, check_text, declare_pages


@dataclass(frozen=True)
class BatchRegistration:
    position: int
    batch_id: str
    manifest: ExpectedPages


def initialize_batch_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS registrations (
            position INTEGER PRIMARY KEY,
            batch_id TEXT NOT NULL UNIQUE,
            manifest TEXT NOT NULL
        )
    ''')


def _read_registration(row: tuple | None) -> BatchRegistration | None:
    if row is None:
        return None
    position, batch_id, manifest = row
    return BatchRegistration(position, batch_id, declare_pages(json.loads(manifest)))


def find_registration(db: sqlite3.Connection, batch_id: str) -> BatchRegistration | None:
    row = db.execute('''
        SELECT position, batch_id, manifest FROM registrations WHERE batch_id = ?
    ''', (batch_id,)).fetchone()
    return _read_registration(row)


def find_registration_at_position(db: sqlite3.Connection, position: int) -> BatchRegistration | None:
    row = db.execute('''
        SELECT position, batch_id, manifest FROM registrations WHERE position = ?
    ''', (position,)).fetchone()
    return _read_registration(row)


def first_registration_after(db: sqlite3.Connection, position: int) -> BatchRegistration | None:
    row = db.execute('''
        SELECT position, batch_id, manifest FROM registrations
        WHERE position > ? ORDER BY position LIMIT 1
    ''', (position,)).fetchone()
    return _read_registration(row)


def register(db: sqlite3.Connection, batch_id: str, pages: Sequence[str]) -> BatchRegistration:
    check_text(batch_id, 'INVALID_BATCH_ID')
    manifest = declare_pages(pages)
    with station_transaction(db):
        original = find_registration(db, batch_id)
        if original is not None:
            if original.manifest != manifest:
                raise StationError('REGISTRATION_CONFLICT', batch_id)
            return original
        inserted = db.execute('INSERT INTO registrations(batch_id, manifest) VALUES (?, ?)',
                              (batch_id, json.dumps(manifest.page_ids, ensure_ascii=False)))
        return BatchRegistration(inserted.lastrowid, batch_id, manifest)
