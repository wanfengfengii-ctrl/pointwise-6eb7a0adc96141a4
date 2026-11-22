import sqlite3
from dataclasses import dataclass

from archive.database import StationError, is_storage_id, station_transaction
from capture.batches import BatchRegistration, find_registration
from capture.completeness import PageCoverage, compute_coverage
from capture.manifest import check_text
from capture.pages import MeasuredPage, accept_page, read_measured_page, read_received_pages


@dataclass(frozen=True)
class FrozenBatch:
    frozen_id: int
    batch_id: str
    pages: tuple[MeasuredPage, ...]


@dataclass(frozen=True)
class ReceiveResult:
    registration: BatchRegistration
    coverage: PageCoverage
    frozen_id: int | None
    replayed: bool


def initialize_freeze_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS frozen_batches (
            frozen_id INTEGER PRIMARY KEY REFERENCES registrations(position)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS frozen_pages (
            frozen_id INTEGER NOT NULL REFERENCES frozen_batches(frozen_id),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            page_id TEXT NOT NULL,
            PRIMARY KEY (frozen_id, ordinal),
            UNIQUE (frozen_id, page_id),
            FOREIGN KEY (frozen_id, page_id) REFERENCES received_pages(batch_position, page_id)
        )
    ''')


def _freeze(db: sqlite3.Connection, registration: BatchRegistration, coverage: PageCoverage) -> int:
    frozen_id = registration.position
    existing = db.execute('SELECT 1 FROM frozen_batches WHERE frozen_id = ?', (frozen_id,)).fetchone()
    if existing is not None:
        return frozen_id
    db.execute('INSERT INTO frozen_batches(frozen_id) VALUES (?)', (frozen_id,))
    db.executemany('INSERT INTO frozen_pages(frozen_id, ordinal, page_id) VALUES (?, ?, ?)',
                   ((frozen_id, ordinal, page) for ordinal, page in enumerate(coverage.received, 1)))
    return frozen_id


def receive(db: sqlite3.Connection, batch_id: str, page_id: str, source: str,
            thickness_um: int | None) -> ReceiveResult:
    check_text(batch_id, 'INVALID_BATCH_ID')
    with station_transaction(db):
        registration = find_registration(db, batch_id)
        if registration is None:
            raise StationError('BATCH_NOT_FOUND', batch_id)
        replayed = accept_page(db, registration, page_id, source, thickness_um)
        originals = read_received_pages(db, registration.position)
        coverage = compute_coverage(registration.manifest, originals)
        frozen_id = _freeze(db, registration, coverage) if coverage.complete else None
        return ReceiveResult(registration, coverage, frozen_id, replayed)


def load_frozen_batch(db: sqlite3.Connection, frozen_id: int) -> FrozenBatch:
    if not is_storage_id(frozen_id):
        raise StationError('INVALID_FROZEN_ID')
    row = db.execute('''
        SELECT r.batch_id, f.frozen_id FROM registrations AS r
        LEFT JOIN frozen_batches AS f ON f.frozen_id = r.position
        WHERE r.position = ?
    ''', (frozen_id,)).fetchone()
    if row is None:
        raise StationError('BATCH_NOT_FOUND', str(frozen_id))
    batch_id, frozen = row
    if frozen is None:
        raise StationError('BATCH_NOT_READY', batch_id)
    pages = db.execute('''
        SELECT p.page_id, p.thickness_hex, p.source FROM frozen_pages AS f
        JOIN received_pages AS p ON p.batch_position = f.frozen_id AND p.page_id = f.page_id
        WHERE f.frozen_id = ? ORDER BY f.ordinal
    ''', (frozen_id,))
    return FrozenBatch(frozen_id, batch_id, tuple(read_measured_page(page) for page in pages))


def find_frozen_batch(db: sqlite3.Connection, batch_id: str) -> FrozenBatch | None:
    row = db.execute('''
        SELECT f.frozen_id FROM frozen_batches AS f
        JOIN registrations AS r ON r.position = f.frozen_id WHERE r.batch_id = ?
    ''', (batch_id,)).fetchone()
    return None if row is None else load_frozen_batch(db, row[0])
