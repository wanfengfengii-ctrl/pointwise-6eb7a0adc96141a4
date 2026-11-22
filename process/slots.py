import sqlite3
from dataclasses import dataclass

from archive.database import StationError
from process.payload import decode_result, encode_result
from process.results import StagedId, StagedResult, ThicknessResult

FIXED_SLOTS = (1, 2)


@dataclass(frozen=True)
class SlotOccupancy:
    slot: int
    generation: int
    frozen_id: int | None

    @property
    def staged_id(self) -> StagedId | None:
        if self.frozen_id is None:
            return None
        return StagedId(self.slot, self.generation)


def initialize_slot_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS process_slots (
            slot INTEGER PRIMARY KEY CHECK (slot IN (1, 2)),
            generation INTEGER NOT NULL CHECK (typeof(generation) = 'integer' AND generation >= 0),
            frozen_id INTEGER UNIQUE REFERENCES frozen_batches(frozen_id),
            payload TEXT,
            CHECK ((frozen_id IS NULL AND payload IS NULL) OR
                   (frozen_id IS NOT NULL AND payload IS NOT NULL AND generation > 0))
        )
    ''')
    db.executemany('INSERT OR IGNORE INTO process_slots(slot, generation) VALUES (?, 0)',
                   ((slot,) for slot in FIXED_SLOTS))


def list_slots(db: sqlite3.Connection) -> tuple[SlotOccupancy, ...]:
    rows = db.execute('SELECT slot, generation, frozen_id FROM process_slots ORDER BY slot')
    return tuple(SlotOccupancy(*row) for row in rows)


def find_staged_result(db: sqlite3.Connection, staged_id: StagedId) -> StagedResult | None:
    row = db.execute('''
        SELECT frozen_id, payload FROM process_slots
        WHERE slot = ? AND generation = ? AND frozen_id IS NOT NULL
    ''', (staged_id.slot, staged_id.generation)).fetchone()
    if row is None:
        return None
    frozen_id, payload = row
    result = decode_result(payload)
    if result.frozen_id != frozen_id:
        raise StationError('SLOT_INCONSISTENT', str(staged_id))
    return StagedResult(staged_id, result)


def write_slot(db: sqlite3.Connection, expected: SlotOccupancy, result: ThicknessResult) -> StagedId:
    staged_id = StagedId(expected.slot, expected.generation + 1)
    update = db.execute('''
        UPDATE process_slots SET generation = ?, frozen_id = ?, payload = ?
        WHERE slot = ? AND generation = ? AND frozen_id IS ?
    ''', (staged_id.generation, result.frozen_id, encode_result(result),
          expected.slot, expected.generation, expected.frozen_id))
    if update.rowcount != 1:
        raise StationError('SLOT_CHANGED', str(expected.slot))
    return staged_id


def clear_slot(db: sqlite3.Connection, staged_id: StagedId) -> bool:
    update = db.execute('''
        UPDATE process_slots SET frozen_id = NULL, payload = NULL
        WHERE slot = ? AND generation = ? AND frozen_id IS NOT NULL
    ''', (staged_id.slot, staged_id.generation))
    return update.rowcount == 1
