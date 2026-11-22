import sqlite3
from dataclasses import dataclass

from archive.database import StationError
from archive.migration import legacy_table_rebuild, table_has_column
from process.payload import decode_result, encode_result
from process.results import StagedId, StagedResult, ThicknessResult

FIXED_SLOTS = (1, 2)


@dataclass(frozen=True)
class SlotOccupancy:
    slot: int
    generation: int
    frozen_id: int | None
    part_frozen_id: int | None = None

    @property
    def staged_id(self) -> StagedId | None:
        if self.frozen_id is None and self.part_frozen_id is None:
            return None
        return StagedId(self.slot, self.generation)

    @property
    def unit_frozen_id(self) -> int | None:
        return self.part_frozen_id if self.part_frozen_id is not None else self.frozen_id


def initialize_slot_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS process_slots (
            slot INTEGER PRIMARY KEY CHECK (slot IN (1, 2)),
            generation INTEGER NOT NULL CHECK (typeof(generation) = 'integer' AND generation >= 0),
            frozen_id INTEGER REFERENCES frozen_batches(frozen_id),
            part_frozen_id INTEGER UNIQUE REFERENCES part_frozen_batches(frozen_id),
            payload TEXT,
            CHECK ((frozen_id IS NULL AND part_frozen_id IS NULL AND payload IS NULL) OR
                   (payload IS NOT NULL AND generation > 0 AND
                    (frozen_id IS NOT NULL) <> (part_frozen_id IS NOT NULL))),
            UNIQUE (frozen_id)
        )
    ''')
    _migrate_legacy_slots(db)
    db.executemany('INSERT OR IGNORE INTO process_slots(slot, generation) VALUES (?, 0)',
                   ((slot,) for slot in FIXED_SLOTS))


def _migrate_legacy_slots(db: sqlite3.Connection) -> None:
    if table_has_column(db, 'process_slots', 'part_frozen_id'):
        return
    with legacy_table_rebuild(db):
        db.execute('ALTER TABLE process_slots RENAME TO process_slots_legacy')
        db.execute('''
            CREATE TABLE process_slots (
                slot INTEGER PRIMARY KEY CHECK (slot IN (1, 2)),
                generation INTEGER NOT NULL CHECK (typeof(generation) = 'integer' AND generation >= 0),
                frozen_id INTEGER REFERENCES frozen_batches(frozen_id),
                part_frozen_id INTEGER UNIQUE REFERENCES part_frozen_batches(frozen_id),
                payload TEXT,
                CHECK ((frozen_id IS NULL AND part_frozen_id IS NULL AND payload IS NULL) OR
                       (payload IS NOT NULL AND generation > 0 AND
                        (frozen_id IS NOT NULL) <> (part_frozen_id IS NOT NULL))),
                UNIQUE (frozen_id)
            )
        ''')
        db.execute('''
            INSERT INTO process_slots(slot, generation, frozen_id, part_frozen_id, payload)
            SELECT slot, generation, frozen_id, NULL, payload FROM process_slots_legacy
        ''')
        # review_loans references the slot table by name and would otherwise
        # keep pointing at the renamed legacy table.
        db.execute('DROP INDEX IF EXISTS one_open_loan_per_seat')
        db.execute('DROP INDEX IF EXISTS open_loan_by_result')
        db.execute('ALTER TABLE review_loans RENAME TO review_loans_legacy')
        db.execute('''
            CREATE TABLE review_loans (
                loan_id INTEGER PRIMARY KEY,
                seat TEXT NOT NULL CHECK (seat = 'qc'),
                slot INTEGER NOT NULL REFERENCES process_slots(slot),
                generation INTEGER NOT NULL CHECK (generation > 0),
                closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1))
            )
        ''')
        db.execute('''
            INSERT INTO review_loans(loan_id, seat, slot, generation, closed)
            SELECT loan_id, seat, slot, generation, closed FROM review_loans_legacy
        ''')
        db.execute('''
            CREATE UNIQUE INDEX one_open_loan_per_seat ON review_loans(seat)
            WHERE closed = 0
        ''')
        db.execute('''
            CREATE INDEX open_loan_by_result ON review_loans(slot, generation)
            WHERE closed = 0
        ''')
        db.execute('DROP TABLE review_loans_legacy')
        db.execute('DROP TABLE process_slots_legacy')


def list_slots(db: sqlite3.Connection) -> tuple[SlotOccupancy, ...]:
    rows = db.execute('''
        SELECT slot, generation, frozen_id, part_frozen_id FROM process_slots ORDER BY slot
    ''')
    return tuple(SlotOccupancy(*row) for row in rows)


def find_staged_result(db: sqlite3.Connection, staged_id: StagedId) -> StagedResult | None:
    row = db.execute('''
        SELECT frozen_id, part_frozen_id, payload FROM process_slots
        WHERE slot = ? AND generation = ?
          AND (frozen_id IS NOT NULL OR part_frozen_id IS NOT NULL)
    ''', (staged_id.slot, staged_id.generation)).fetchone()
    if row is None:
        return None
    frozen_id, part_frozen_id, payload = row
    result = decode_result(payload)
    expected_unit_id = part_frozen_id if part_frozen_id is not None else frozen_id
    stored_unit_id = result.part_frozen_id if result.part_frozen_id is not None else result.frozen_id
    if stored_unit_id != expected_unit_id:
        raise StationError('SLOT_INCONSISTENT', str(staged_id))
    return StagedResult(staged_id, result)


def write_slot(db: sqlite3.Connection, expected: SlotOccupancy, result: ThicknessResult) -> StagedId:
    staged_id = StagedId(expected.slot, expected.generation + 1)
    is_part = result.part_frozen_id is not None
    update = db.execute('''
        UPDATE process_slots
        SET generation = ?,
            frozen_id = CASE WHEN ? = 1 THEN NULL ELSE ? END,
            part_frozen_id = CASE WHEN ? = 1 THEN ? ELSE NULL END,
            payload = ?
        WHERE slot = ? AND generation = ?
          AND frozen_id IS ? AND part_frozen_id IS ?
    ''', (staged_id.generation, 1 if is_part else 0,
          None if is_part else result.frozen_id,
          1 if is_part else 0, result.part_frozen_id if is_part else None,
          encode_result(result),
          expected.slot, expected.generation,
          expected.frozen_id, expected.part_frozen_id))
    if update.rowcount != 1:
        raise StationError('SLOT_CHANGED', str(expected.slot))
    return staged_id


def clear_slot(db: sqlite3.Connection, staged_id: StagedId) -> bool:
    update = db.execute('''
        UPDATE process_slots SET frozen_id = NULL, part_frozen_id = NULL, payload = NULL
        WHERE slot = ? AND generation = ?
          AND (frozen_id IS NOT NULL OR part_frozen_id IS NOT NULL)
    ''', (staged_id.slot, staged_id.generation))
    return update.rowcount == 1
