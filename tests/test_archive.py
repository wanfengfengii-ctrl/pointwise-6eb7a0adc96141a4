from archive.database import initialize_station
from archive.retrieve import get
from capture.batches import register
from capture.freeze import receive
from issue.commit import issue
from process.staging import stage
from review.admission import preview
from review.closing import close
from review.readout import read_preview

from tests.fixtures import StationCase


class ArchiveTests(StationCase):
    def test_reopen_missing_batch(self):
        register(self.db, 'A', ['tail', 'head'])
        frozen = self.receive_all('A', [('head', 'scan/head', None), ('tail', 'scan/tail', None)])
        issued = issue(self.db, 'A', stage(self.db, frozen).staged_id)
        self.db.close()
        reopened = self.open_station()
        archived = get(reopened, issued.record_id)
        self.assert_result(archived.result, batch='A', pages=[('tail', None, 'scan/tail'),
                           ('head', None, 'scan/head')], spread=None, missing=2)
        self.assertEqual(archived.receipt, issued.receipt)

    def test_receipt_matches_record(self):
        register(self.db, 'A', ['1', '2'])
        register(self.db, 'B', ['1'])
        a = self.receive_all('A', [('2', 'source/A/2', None), ('1', 'source/A/1', None)])
        b = self.receive_all('B', [('1', 'source/B/1', 11)])
        a_stage = stage(self.db, a).staged_id
        a_issued = issue(self.db, 'A', a_stage)
        b_stage = stage(self.db, b).staged_id
        b_issued = issue(self.db, 'B', b_stage)
        self.assertNotEqual(a_issued.receipt.receipt_id, b_issued.receipt.receipt_id)
        self.db.close()
        reopened = self.open_station()
        a_record = get(reopened, a_issued.record_id)
        b_record = get(reopened, b_issued.record_id)
        self.assertEqual(a_record.receipt, a_issued.receipt)
        self.assertEqual(b_record.receipt, b_issued.receipt)
        self.assert_result(a_record.result, batch='A', pages=[('1', None, 'source/A/1'),
                           ('2', None, 'source/A/2')], spread=None, missing=2)
        self.assert_result(b_record.result, batch='B', pages=[('1', 11, 'source/B/1')], spread=0, missing=0)

    def test_unknown_result(self):
        register(self.db, 'A', ['1'])
        frozen = self.receive_all('A', [('1', 'scan/A/1', 9)])
        stage(self.db, frozen)
        with self.error('RECORD_NOT_FOUND'):
            get(self.db, 1)
        issued = issue(self.db, 'A', stage(self.db, frozen).staged_id)
        with self.error('RECORD_NOT_FOUND'):
            get(self.db, issued.record_id + 1)

    def test_reinitialize_preserves_state(self):
        a = register(self.db, 'A', ['1'])
        register(self.db, 'B', ['1'])
        register(self.db, 'C', ['1', '2'])
        a_frozen = self.receive_all('A', [('1', 'scan/A/1', 9)])
        a_staged = stage(self.db, a_frozen).staged_id
        a_issued = issue(self.db, 'A', a_staged)
        archived_a = get(self.db, a_issued.record_id)
        b_frozen = self.receive_all('B', [('1', 'scan/B/1', 11)])
        b_stage = stage(self.db, b_frozen).staged_id
        b_review = preview(self.db, 'qc', b_stage)
        pending = receive(self.db, 'C', '1', 'scan/C/1', None)
        initialize_station(self.db)
        initialize_station(self.db)
        self.db.close()
        reopened = self.open_station()

        self.assertEqual(register(reopened, 'A', ['1']), a)
        self.assertEqual(get(reopened, a_issued.record_id), archived_a)
        self.assertEqual(read_preview(reopened, b_review.loan.loan_id), b_review.result)
        reused = stage(reopened, b_frozen)
        self.assertTrue(reused.reused)
        self.assertEqual(reused.staged_id, b_stage)
        replay = receive(reopened, 'C', '1', 'scan/C/1', None)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.coverage, pending.coverage)
        b_issued = issue(reopened, 'B', b_stage)
        self.assertTrue(close(reopened, b_review.loan.loan_id).slot_released)
        c_frozen = receive(reopened, 'C', '2', 'scan/C/2', 24).frozen_id
        c_stage = stage(reopened, c_frozen).staged_id
        c_review = preview(reopened, 'qc', c_stage)
        c_issued = issue(reopened, 'C', c_stage)
        self.assertGreater(c_review.loan.loan_id, b_review.loan.loan_id)
        self.assertGreater(c_issued.record_id, b_issued.record_id)
        self.assertGreater(b_issued.record_id, a_issued.record_id)
        self.assertGreater(c_issued.receipt.receipt_id, b_issued.receipt.receipt_id)
        actual = get(reopened, c_issued.record_id)
        self.assertEqual(actual.receipt, c_issued.receipt)
        self.assert_result(actual.result, batch='C', pages=[('1', None, 'scan/C/1'), ('2', 24, 'scan/C/2')],
                           spread=0, missing=1)

    def test_exact_integer_range_survives_reopen(self):
        register(self.db, 'A', ['a', 'b', 'c'])
        lower = -(1 << 16000)
        upper = 1 << 16002
        frozen = self.receive_all('A', [('b', 'scan/b', None), ('c', 'scan/c', upper), ('a', 'scan/a', lower)])
        issued = issue(self.db, 'A', stage(self.db, frozen).staged_id)
        self.db.close()
        archived = get(self.open_station(), issued.record_id)
        self.assert_result(archived.result, batch='A', pages=[('a', lower, 'scan/a'), ('b', None, 'scan/b'),
                           ('c', upper, 'scan/c')], spread=5 << 16000, missing=1)
