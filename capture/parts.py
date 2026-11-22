import sqlite3
from dataclasses import dataclass

from archive.database import StationError
from capture.batches import BatchRegistration, find_registration_at_position
from capture.completeness import compute_coverage
from capture.pages import MeasuredPage, read_received_pages

FRONT_ORDINAL = 1
BACK_ORDINAL = 2


@dataclass(frozen=True)
class BatchPart:
    part_id: int
    parent_position: int
    ordinal: int
    after_page: str


@dataclass(frozen=True)
class PartStatus:
    part_id: int
    missing: tuple[str, ...]
    frozen_id: int | None


def initialize_part_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS batch_parts (
            part_id INTEGER PRIMARY KEY,
            parent_position INTEGER NOT NULL REFERENCES registrations(position),
            ordinal INTEGER NOT NULL CHECK (ordinal IN (1, 2)),
            after_page TEXT NOT NULL,
            UNIQUE (parent_position, ordinal)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS part_frozen_batches (
            frozen_id INTEGER PRIMARY KEY,
            part_id INTEGER NOT NULL UNIQUE REFERENCES batch_parts(part_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS part_sequences (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            next_part_id INTEGER NOT NULL CHECK (next_part_id > 0),
            next_frozen_id INTEGER NOT NULL CHECK (next_frozen_id > 0)
        )
    ''')
    db.execute('''
        INSERT OR IGNORE INTO part_sequences(singleton, next_part_id, next_frozen_id)
        VALUES (1, 1, 1)
    ''')


def split_manifest(manifest_page_ids: tuple[str, ...],
                   after_page: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    boundary = manifest_page_ids.index(after_page)
    return manifest_page_ids[:boundary + 1], manifest_page_ids[boundary + 1:]


def part_page_ids(registration: BatchRegistration, part: BatchPart) -> tuple[str, ...]:
    front, back = split_manifest(registration.manifest.page_ids, part.after_page)
    return front if part.ordinal == FRONT_ORDINAL else back


def find_split(db: sqlite3.Connection, parent_position: int) -> tuple[BatchPart, BatchPart] | None:
    rows = db.execute('''
        SELECT part_id, parent_position, ordinal, after_page FROM batch_parts
        WHERE parent_position = ? ORDER BY ordinal
    ''', (parent_position,)).fetchall()
    if not rows:
        return None
    front, back = (BatchPart(*row) for row in rows)
    if front.ordinal != FRONT_ORDINAL or back.ordinal != BACK_ORDINAL:
        raise StationError('SPLIT_INCONSISTENT', str(parent_position))
    return front, back


def find_batch_part(db: sqlite3.Connection, part_id: int) -> BatchPart | None:
    row = db.execute('''
        SELECT part_id, parent_position, ordinal, after_page FROM batch_parts WHERE part_id = ?
    ''', (part_id,)).fetchone()
    return None if row is None else BatchPart(*row)


def part_frozen_id(db: sqlite3.Connection, part_id: int) -> int | None:
    row = db.execute('''
        SELECT frozen_id FROM part_frozen_batches WHERE part_id = ?
    ''', (part_id,)).fetchone()
    return None if row is None else row[0]


def part_is_issued(db: sqlite3.Connection, part_id: int) -> bool:
    return db.execute('SELECT 1 FROM archive_index WHERE part_id = ?',
                      (part_id,)).fetchone() is not None


def _advance(db: sqlite3.Connection, column: str, count: int) -> int:
    current = db.execute(
        f'SELECT {column} FROM part_sequences WHERE singleton = 1').fetchone()[0]
    update = db.execute(
        f'UPDATE part_sequences SET {column} = ? '
        f'WHERE singleton = 1 AND {column} = ?',
        (current + count, current))
    if update.rowcount != 1:
        raise StationError('PART_SEQUENCE_CHANGED')
    return current


def insert_split(db: sqlite3.Connection, registration: BatchRegistration,
                 after_page: str) -> tuple[BatchPart, BatchPart]:
    first_part_id = _advance(db, 'next_part_id', 2)
    front_id, back_id = first_part_id, first_part_id + 1
    db.executemany('''
        INSERT INTO batch_parts(part_id, parent_position, ordinal, after_page) VALUES (?, ?, ?, ?)
    ''', (
        (front_id, registration.position, FRONT_ORDINAL, after_page),
        (back_id, registration.position, BACK_ORDINAL, after_page),
    ))
    return (
        BatchPart(front_id, registration.position, FRONT_ORDINAL, after_page),
        BatchPart(back_id, registration.position, BACK_ORDINAL, after_page),
    )


def insert_part_freeze(db: sqlite3.Connection, part: BatchPart) -> int:
    frozen_id = _advance(db, 'next_frozen_id', 1)
    db.execute('INSERT INTO part_frozen_batches(frozen_id, part_id) VALUES (?, ?)',
               (frozen_id, part.part_id))
    return frozen_id


def _part_missing(registration: BatchRegistration, part: BatchPart,
                  received: tuple[MeasuredPage, ...]) -> tuple[str, ...]:
    received_ids = {page.page_id for page in received}
    return tuple(page_id for page_id in part_page_ids(registration, part)
                 if page_id not in received_ids)


def freeze_completed_parts(db: sqlite3.Connection, registration: BatchRegistration,
                           parts: tuple[BatchPart, BatchPart]) -> None:
    """Freeze every segment whose registered pages have all been received."""
    received = read_received_pages(db, registration.position)
    for part in parts:
        if part_frozen_id(db, part.part_id) is not None:
            continue
        if _part_missing(registration, part, received):
            continue
        insert_part_freeze(db, part)


def read_part_status(db: sqlite3.Connection, registration: BatchRegistration,
                     parts: tuple[BatchPart, BatchPart]) -> tuple[PartStatus, PartStatus]:
    received = read_received_pages(db, registration.position)
    return tuple(
        PartStatus(part.part_id,
                   _part_missing(registration, part, received),
                   part_frozen_id(db, part.part_id))
        for part in parts
    )


def load_part_pages(db: sqlite3.Connection, registration: BatchRegistration,
                    part: BatchPart) -> tuple[MeasuredPage, ...]:
    wanted = part_page_ids(registration, part)
    by_page = {page.page_id: page
               for page in read_received_pages(db, registration.position)}
    return tuple(by_page[page_id] for page_id in wanted)


def parent_coverage(registration: BatchRegistration,
                    received: tuple[MeasuredPage, ...]):
    return compute_coverage(registration.manifest, received)


def load_parent_registration(db: sqlite3.Connection, part: BatchPart) -> BatchRegistration:
    registration = find_registration_at_position(db, part.parent_position)
    if registration is None:
        raise StationError('BATCH_NOT_FOUND', str(part.parent_position))
    return registration
