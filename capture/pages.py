import sqlite3
from dataclasses import dataclass

from archive.database import StationError
from capture.batches import BatchRegistration
from capture.manifest import check_text


@dataclass(frozen=True)
class MeasuredPage:
    page_id: str
    thickness_um: int | None
    source: str


def initialize_page_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS received_pages (
            batch_position INTEGER NOT NULL REFERENCES registrations(position),
            page_id TEXT NOT NULL,
            thickness_hex TEXT,
            source TEXT NOT NULL,
            PRIMARY KEY (batch_position, page_id)
        )
    ''')


def encode_measurement(value: int | None) -> str | None:
    # Hex text preserves exact integers beyond SQLite's integer range.
    return None if value is None else format(value, 'x')


def decode_measurement(encoded: str | None) -> int | None:
    return None if encoded is None else int(encoded, 16)


def read_measured_page(row: tuple) -> MeasuredPage:
    page_id, thickness_hex, source = row
    return MeasuredPage(page_id, decode_measurement(thickness_hex), source)


def accept_page(db: sqlite3.Connection, registration: BatchRegistration, page_id: str,
                source: str, thickness_um: int | None) -> bool:
    """Accept an original page, returning whether it was already received."""
    check_text(page_id, 'INVALID_PAGE_ID')
    if not registration.manifest.contains(page_id):
        raise StationError('FOREIGN_PAGE', page_id)
    check_text(source, 'INVALID_SOURCE')
    if thickness_um is not None and type(thickness_um) is not int:
        raise StationError('INVALID_THICKNESS', 'expected integer micrometres or None')

    existing = db.execute('''
        SELECT page_id, thickness_hex, source FROM received_pages
        WHERE batch_position = ? AND page_id = ?
    ''', (registration.position, page_id)).fetchone()
    if existing is not None:
        if read_measured_page(existing) != MeasuredPage(page_id, thickness_um, source):
            raise StationError('PAGE_CONFLICT', page_id)
        return True
    db.execute('''
        INSERT INTO received_pages(batch_position, page_id, thickness_hex, source)
        VALUES (?, ?, ?, ?)
    ''', (registration.position, page_id, encode_measurement(thickness_um), source))
    return False


def read_received_pages(db: sqlite3.Connection, batch_position: int) -> tuple[MeasuredPage, ...]:
    rows = db.execute('''
        SELECT page_id, thickness_hex, source FROM received_pages
        WHERE batch_position = ? ORDER BY rowid
    ''', (batch_position,))
    return tuple(read_measured_page(row) for row in rows)
