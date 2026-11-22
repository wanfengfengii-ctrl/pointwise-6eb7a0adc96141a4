import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.database import StationError, open_station_database
from capture.batches import register
from capture.freeze import receive
from demo import read_sample
from issue.order import current_frontier
from process.staging import stage
from review.admission import preview


class StationCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='beiting-test-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'station.sqlite'
        self.db = self.open_station()

    def open_station(self):
        db = open_station_database(self.path)
        self.addCleanup(db.close)
        return db

    @contextmanager
    def error(self, code):
        with self.assertRaises(StationError) as caught:
            yield
        self.assertEqual(caught.exception.code, code)

    def receive_all(self, batch_id, arrivals, *, db=None):
        db = self.db if db is None else db
        for page_id, source, value in arrivals:
            received = receive(db, batch_id, page_id, source, value)
        self.assertTrue(received.coverage.complete)
        self.assertIsNotNone(received.frozen_id)
        return received.frozen_id

    def assert_result(self, result, *, batch, pages, spread, missing):
        self.assertEqual(result.batch_id, batch)
        self.assertEqual([(page.page_id, page.thickness_um, page.source) for page in result.pages], pages)
        self.assertEqual(result.range_um, spread)
        self.assertEqual(result.missing_count, missing)

    def assert_sample(self, result, batch, expected):
        expected_result = expected[batch]
        self.assert_result(result, batch=batch, pages=[tuple(row) for row in expected_result['pages']],
                           spread=expected_result['range_um'], missing=expected_result['missing_count'])

    def begin_sample(self):
        sample, expected = read_sample()
        for batch, manifest in sample['registrations'].items():
            register(self.db, batch, manifest)
        arrivals = sample['arrivals']
        partial = receive(self.db, 'A', *arrivals['A'][0])
        self.assertEqual(partial.coverage.missing, ('2',))
        self.assertIsNone(partial.frozen_id)
        b = self.receive_all('B', arrivals['B'])
        b_staged = stage(self.db, b).staged_id
        b_preview = preview(self.db, 'qc', b_staged)
        c = self.receive_all('C', arrivals['C'])
        return arrivals, expected, b_staged, b_preview, c

    def request_later_stage(self, frozen_id):
        try:
            return stage(self.db, frozen_id)
        except StationError as error:
            self.assertEqual(error.code, 'NO_SLOT')
            return None

    def committed_state(self):
        # Use another connection to inspect what white shift can observe.
        observer = sqlite3.connect(self.path)
        try:
            frontier = current_frontier(observer)
            keys = observer.execute('SELECT batch_id FROM archive_index ORDER BY record_id').fetchall()
            payloads = observer.execute('SELECT record_id FROM archive_records ORDER BY record_id').fetchall()
            receipts = observer.execute('SELECT batch_id FROM issue_receipts ORDER BY receipt_id').fetchall()
            return frontier, keys, payloads, receipts
        finally:
            observer.close()
