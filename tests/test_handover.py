import sqlite3

from archive.database import StationError
from archive.retrieve import get
from capture.batches import register
from capture.freeze import load_frozen_batch, receive
from issue.commit import issue
from process.slots import list_slots
from process.staging import stage
from review.admission import preview
from review.closing import close
from review.readout import read_preview

from tests.fixtures import StationCase


class HandoverTests(StationCase):
    def test_issue_order(self):
        _, _, b_stage, _, _ = self.begin_sample()
        with self.error('OUT_OF_ORDER'):
            issue(self.db, 'B', b_stage)
        with self.error('BATCH_NOT_READY'):
            issue(self.db, 'A', b_stage)
        self.assertEqual(self.committed_state(), (0, [], [], []))
        with self.error('RECORD_NOT_FOUND'):
            get(self.db, 1)

    def test_mismatched_stage(self):
        arrivals, _, b_stage, _, _ = self.begin_sample()
        a_id = receive(self.db, 'A', *arrivals['A'][1]).frozen_id
        a = stage(self.db, a_id)
        with self.error('STAGE_BATCH_MISMATCH'):
            issue(self.db, 'A', b_stage)
        self.assertEqual(self.committed_state(), (0, [], [], []))
        issued = issue(self.db, 'A', a.staged_id)
        self.assertEqual(get(self.db, issued.record_id).result.batch_id, 'A')
        with self.error('ALREADY_ISSUED'):
            issue(self.db, 'A', a.staged_id)
        self.assertEqual(self.committed_state(), (1, [('A',)], [(issued.record_id,)], [('A',)]))

    def test_head_can_stage_with_open_preview(self):
        arrivals, expected, b_stage, b_review, c_frozen = self.begin_sample()
        c_first = self.request_later_stage(c_frozen)
        a_frozen = receive(self.db, 'A', *arrivals['A'][1]).frozen_id
        try:
            a_stage = stage(self.db, a_frozen)
        except StationError as error:
            if error.code in {'NO_SLOT', 'EARLY_BATCH_NO_SLOT'}:
                self.fail(f'EARLY_BATCH_NO_SLOT: frozen A could not stage: {error}')
            raise
        self.assertIsNotNone(a_stage.staged_id)
        if c_first is not None:
            self.assertEqual(a_stage.evicted_id, c_first.staged_id)
        self.assert_sample(read_preview(self.db, b_review.loan.loan_id), 'B', expected)

        a_issued = issue(self.db, 'A', a_stage.staged_id)
        b_issued = issue(self.db, 'B', b_stage)
        close(self.db, b_review.loan.loan_id)
        c_issued = issue(self.db, 'C', stage(self.db, c_frozen).staged_id)
        issued = [a_issued, b_issued, c_issued]
        ids = [item.record_id for item in issued]
        self.assertEqual(ids, sorted(set(ids)))
        reopened = self.open_station()
        for batch, original in zip(('A', 'B', 'C'), issued, strict=True):
            archived = get(reopened, original.record_id)
            self.assert_sample(archived.result, batch, expected)
            self.assertEqual(archived.receipt, original.receipt)
        self.assertEqual(self.committed_state(), (3, [('A',), ('B',), ('C',)],
                                                 [(number,) for number in ids], [('A',), ('B',), ('C',)]))

    def test_recompute_evicted(self):
        arrivals, expected, b_stage, b_review, c_frozen = self.begin_sample()
        original_c = load_frozen_batch(self.db, c_frozen)
        c_first = self.request_later_stage(c_frozen)
        a = stage(self.db, receive(self.db, 'A', *arrivals['A'][1]).frozen_id)
        if c_first is not None:
            self.assertEqual(a.evicted_id, c_first.staged_id)
        issue(self.db, 'A', a.staged_id)
        issue(self.db, 'B', b_stage)
        close(self.db, b_review.loan.loan_id)
        new_c = stage(self.db, c_frozen)
        if c_first is not None:
            self.assertNotEqual(new_c.staged_id, c_first.staged_id)
            with self.error('STAGED_UNAVAILABLE'):
                preview(self.db, 'qc', c_first.staged_id)
            with self.error('STAGED_UNAVAILABLE'):
                issue(self.db, 'C', c_first.staged_id)
        c_review = preview(self.db, 'qc', new_c.staged_id)
        self.assertEqual(c_review.result.frozen_id, c_frozen)
        self.assertEqual(c_review.result.pages, original_c.pages)
        self.assert_sample(c_review.result, 'C', expected)
        issued = issue(self.db, 'C', new_c.staged_id)
        self.assert_sample(get(self.db, issued.record_id).result, 'C', expected)

    def test_open_loan_survives_issue(self):
        arrivals, expected, b_stage, b_review, c_frozen = self.begin_sample()
        a = stage(self.db, receive(self.db, 'A', *arrivals['A'][1]).frozen_id)
        issue(self.db, 'A', a.staged_id)
        b_issued = issue(self.db, 'B', b_stage)
        self.assert_sample(get(self.db, b_issued.record_id).result, 'B', expected)
        self.assert_sample(read_preview(self.db, b_review.loan.loan_id), 'B', expected)
        self.assertIn(b_stage, [slot.staged_id for slot in list_slots(self.db)])
        self.assertTrue(close(self.db, b_review.loan.loan_id).slot_released)
        self.assertIsNone(next(slot for slot in list_slots(self.db) if slot.slot == b_stage.slot).staged_id)
        c = stage(self.db, c_frozen)
        self.assert_sample(preview(self.db, 'qc', c.staged_id).result, 'C', expected)
        self.assertEqual(get(self.db, b_issued.record_id).receipt, b_issued.receipt)

    def test_issue_commit_failure(self):
        arrivals, expected, b_stage, b_review, _ = self.begin_sample()
        a = stage(self.db, receive(self.db, 'A', *arrivals['A'][1]).frozen_id)
        slots_before = list_slots(self.db)
        self.db.execute('''
            CREATE TRIGGER block_frontier BEFORE UPDATE OF issued_through ON issue_order
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM archive_index AS k
                    JOIN archive_records AS p ON p.record_id = k.record_id
                    JOIN issue_receipts AS r ON r.record_id = k.record_id
                    WHERE k.batch_id = 'A' AND r.batch_id = 'A'
                ) THEN RAISE(ABORT, 'fixture: frontier update refused')
                  ELSE RAISE(ABORT, 'fixture: archive or receipt missing before frontier update') END;
            END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture: frontier update refused'):
            issue(self.db, 'A', a.staged_id)
        self.assertEqual(self.committed_state(), (0, [], [], []))
        with self.error('RECORD_NOT_FOUND'):
            get(self.open_station(), 1)
        self.assertEqual(list_slots(self.db), slots_before)
        self.assert_sample(read_preview(self.db, b_review.loan.loan_id), 'B', expected)
        self.db.execute('DROP TRIGGER block_frontier')
        a_issued = issue(self.db, 'A', a.staged_id)
        self.assertEqual(self.committed_state(), (1, [('A',)], [(a_issued.record_id,)], [('A',)]))
        b_issued = issue(self.db, 'B', b_stage)
        reopened = self.open_station()
        for batch, issued in [('A', a_issued), ('B', b_issued)]:
            archived = get(reopened, issued.record_id)
            self.assert_sample(archived.result, batch, expected)
            self.assertEqual(archived.receipt, issued.receipt)
        self.assertEqual(self.committed_state(), (2, [('A',), ('B',)],
                          [(a_issued.record_id,), (b_issued.record_id,)], [('A',), ('B',)]))

    def test_failed_slot_write_preserves_occupants(self):
        arrivals, expected, _, b_review, c_frozen = self.begin_sample()
        c_first = self.request_later_stage(c_frozen)
        a_frozen = receive(self.db, 'A', *arrivals['A'][1]).frozen_id
        before = list_slots(self.db)
        self.db.execute('''
            CREATE TRIGGER block_slot BEFORE UPDATE OF generation ON process_slots
            BEGIN SELECT RAISE(ABORT, 'fixture: slot update refused'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture: slot update refused'):
            stage(self.db, a_frozen)
        self.assertEqual(list_slots(self.db), before)
        self.assert_sample(read_preview(self.db, b_review.loan.loan_id), 'B', expected)
        self.db.execute('DROP TRIGGER block_slot')
        if c_first is not None:
            self.assertEqual(stage(self.db, c_frozen).staged_id, c_first.staged_id)
        staged = stage(self.db, a_frozen)
        self.assertIsNotNone(staged.staged_id)

    def test_head_can_reclaim_with_an_earlier_signed_preview(self):
        for batch in ('A', 'B', 'C'):
            register(self.db, batch, ['1'])
        a_frozen = self.receive_all('A', [('1', 'scan/A/1', 7)])
        a_stage = stage(self.db, a_frozen).staged_id
        a_review = preview(self.db, 'qc', a_stage)
        issue(self.db, 'A', a_stage)
        c_frozen = self.receive_all('C', [('1', 'scan/C/1', 20)])
        self.request_later_stage(c_frozen)
        b_frozen = self.receive_all('B', [('1', 'scan/B/1', 11)])
        b_stage = stage(self.db, b_frozen).staged_id
        self.assertNotEqual(b_stage.slot, a_stage.slot)
        self.assertEqual(read_preview(self.db, a_review.loan.loan_id), a_review.result)
        self.assert_result(get(self.db, issue(self.db, 'B', b_stage).record_id).result,
                           batch='B', pages=[('1', 11, 'scan/B/1')], spread=0, missing=0)
