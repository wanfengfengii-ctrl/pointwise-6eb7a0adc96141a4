import sqlite3
from dataclasses import dataclass

from archive.database import StationError, is_storage_id
from capture.pages import MeasuredPage
from capture.parts import BatchPart, find_batch_part, load_parent_registration, load_part_pages, part_frozen_id


@dataclass(frozen=True)
class FrozenPart:
    frozen_id: int
    part_id: int
    batch_id: str
    pages: tuple[MeasuredPage, ...]


def load_frozen_part(db: sqlite3.Connection, part_frozen_id: int) -> FrozenPart:
    if not is_storage_id(part_frozen_id):
        raise StationError('INVALID_FROZEN_ID')
    row = db.execute('''
        SELECT part_id FROM part_frozen_batches WHERE frozen_id = ?
    ''', (part_frozen_id,)).fetchone()
    if row is None:
        raise StationError('PART_NOT_READY', str(part_frozen_id))
    part_id = row[0]
    part = find_batch_part(db, part_id)
    if part is None:
        raise StationError('PART_NOT_FOUND', str(part_id))
    registration = load_parent_registration(db, part)
    pages = load_part_pages(db, registration, part)
    return FrozenPart(part_frozen_id, part_id, registration.batch_id, pages)


def frozen_part_for_id(db: sqlite3.Connection, part_id: int) -> FrozenPart | None:
    frozen_id = part_frozen_id(db, part_id)
    if frozen_id is None:
        return None
    return load_frozen_part(db, frozen_id)


def part_for_stage(db: sqlite3.Connection, part_id: int) -> tuple[BatchPart, FrozenPart]:
    part = find_batch_part(db, part_id)
    if part is None:
        raise StationError('PART_NOT_FOUND', str(part_id))
    frozen = frozen_part_for_id(db, part_id)
    if frozen is None:
        raise StationError('PART_NOT_READY', str(part_id))
    return part, frozen
