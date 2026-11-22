import sqlite3

from archive.index import RecordKey
from process.payload import decode_result, encode_result
from process.results import ThicknessResult


def initialize_record_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS archive_records (
            record_id INTEGER PRIMARY KEY REFERENCES archive_index(record_id),
            payload TEXT NOT NULL
        )
    ''')


def store_record_payload(db: sqlite3.Connection, key: RecordKey, result: ThicknessResult) -> None:
    db.execute('INSERT INTO archive_records(record_id, payload) VALUES (?, ?)',
               (key.record_id, encode_result(result)))


def load_record_payload(db: sqlite3.Connection, key: RecordKey) -> ThicknessResult | None:
    row = db.execute('SELECT payload FROM archive_records WHERE record_id = ?', (key.record_id,)).fetchone()
    return None if row is None else decode_result(row[0])
