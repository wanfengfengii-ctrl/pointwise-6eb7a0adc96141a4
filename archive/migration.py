import sqlite3
from contextlib import contextmanager


@contextmanager
def legacy_table_rebuild(db: sqlite3.Connection):
    """Rebuild a table shape on an older working library.

    SQLite ignores ``PRAGMA foreign_keys`` inside a transaction, and DDL on
    an autocommit connection still opens a transaction. The rebuild therefore
    commits any pending DDL first, toggles foreign keys while idle, runs the
    caller's rebuild in one explicit transaction, and restores the pragma.
    """
    if db.in_transaction:
        db.commit()
    db.execute('PRAGMA foreign_keys = OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            db.rollback()
            raise
        db.commit()
    finally:
        db.execute('PRAGMA foreign_keys = ON')


def table_has_column(db: sqlite3.Connection, table: str, column: str) -> bool:
    return column in {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
