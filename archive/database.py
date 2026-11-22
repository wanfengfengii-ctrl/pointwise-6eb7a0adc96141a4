import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from os import PathLike


class StationError(Exception):
    """A rejected station operation with a stable business error code."""

    def __init__(self, code: str, detail: str = ''):
        self.code = code
        self.detail = detail
        super().__init__(f'{code}: {detail}' if detail else code)


def is_storage_id(value: object) -> bool:
    return type(value) is int and 0 < value <= (1 << 63) - 1


@contextmanager
def station_transaction(db: sqlite3.Connection, *, write: bool = True) -> Iterator[None]:
    if db.in_transaction:
        raise StationError('TRANSACTION_ACTIVE', 'use an idle station connection')
    db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def initialize_station(db: sqlite3.Connection) -> None:
    # Store modules use the transaction helper above for their public commands.
    from archive.index import initialize_record_index
    from archive.records import initialize_record_store
    from capture.batches import initialize_batch_store
    from capture.freeze import initialize_freeze_store
    from capture.pages import initialize_page_store
    from capture.parts import initialize_part_store
    from issue.order import initialize_issue_order
    from issue.receipts import initialize_receipt_store
    from process.slots import initialize_slot_store
    from review.loans import initialize_loan_store

    if db.in_transaction:
        raise StationError('TRANSACTION_ACTIVE', 'initialize an idle station connection')
    db.execute('PRAGMA foreign_keys = ON')
    # Table rebuilds for older working libraries manage their own
    # transactions so SQLite can toggle foreign keys around the migration.
    initialize_batch_store(db)
    initialize_page_store(db)
    initialize_part_store(db)
    initialize_freeze_store(db)
    initialize_slot_store(db)
    initialize_loan_store(db)
    initialize_issue_order(db)
    initialize_record_index(db)
    initialize_record_store(db)
    initialize_receipt_store(db)
    if db.in_transaction:
        db.commit()


def open_station_database(path: str | PathLike[str]) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        initialize_station(db)
    except BaseException:
        db.close()
        raise
    return db
