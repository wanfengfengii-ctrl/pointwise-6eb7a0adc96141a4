from archive.retrieve import get
from capture.batches import register
from issue.commit import issue
from process.results import StagedId
from process.staging import stage
from review.admission import preview
from review.closing import close
from review.readout import read_preview

from tests.fixtures import StationCase


class ReviewTests(StationCase):
    def test_later_batch_preview(self):
        _, expected, b_stage, loan, _ = self.begin_sample()
        self.assertEqual(loan.loan.staged_id, b_stage)
        self.assert_sample(loan.result, 'B', expected)
        self.assert_sample(read_preview(self.db, loan.loan.loan_id), 'B', expected)
        self.assertEqual(self.committed_state(), (0, [], [], []))

    def test_exclusive_seat(self):
        _, expected, b_stage, original, _ = self.begin_sample()
        loan_id = original.loan.loan_id
        with self.error('SEAT_BUSY'):
            preview(self.db, 'qc', b_stage)
        with self.error('INVALID_SEAT'):
            preview(self.db, 'other', b_stage)
        self.assert_sample(read_preview(self.db, loan_id), 'B', expected)
        with self.error('LOAN_NOT_FOUND'):
            close(self.db, loan_id + 100)
        ended = close(self.db, loan_id)
        self.assertTrue(ended.closed_now)
        self.assertFalse(ended.slot_released)
        with self.error('LOAN_CLOSED'):
            read_preview(self.db, loan_id)
        with self.error('STAGED_UNAVAILABLE'):
            preview(self.db, 'qc', StagedId(b_stage.slot, b_stage.generation + 100))
        second = preview(self.db, 'qc', b_stage)
        self.assertNotEqual(second.loan.loan_id, loan_id)
        self.assert_sample(second.result, 'B', expected)

    def test_stale_close_after_reuse(self):
        # X fills the other slot so C must use B's old physical slot.
        for batch, pages in [('B', ['1', '2']), ('X', ['1']), ('C', ['1', '2'])]:
            register(self.db, batch, pages)
        b = self.receive_all('B', [('2', 'scan/B/2', 11), ('1', 'scan/B/1', 7)])
        x = self.receive_all('X', [('1', 'scan/X/1', 8)])
        c = self.receive_all('C', [('1', 'scan/C/1', 20), ('2', 'scan/C/2', 24)])
        b_stage = stage(self.db, b).staged_id
        b_review = preview(self.db, 'qc', b_stage)
        b_issued = issue(self.db, 'B', b_stage)
        x_stage = stage(self.db, x).staged_id
        self.assertNotEqual(b_stage.slot, x_stage.slot)
        self.assertTrue(close(self.db, b_review.loan.loan_id).slot_released)
        c_stage = stage(self.db, c).staged_id
        self.assertEqual(c_stage.slot, b_stage.slot)
        self.assertNotEqual(c_stage, b_stage)
        c_review = preview(self.db, 'qc', c_stage)
        replay = close(self.db, b_review.loan.loan_id)
        self.assertFalse(replay.closed_now)
        self.assertFalse(replay.slot_released)

        with self.subTest(case='v6.replay_does_not_touch_replacement'):
            actual = read_preview(self.db, c_review.loan.loan_id)
            self.assert_result(actual, batch='C', pages=[('1', 20, 'scan/C/1'), ('2', 24, 'scan/C/2')],
                               spread=4, missing=0)
            self.assertEqual(actual.range_um, b_review.result.range_um)
            self.assertNotEqual(actual.pages, b_review.result.pages)
            issue(self.db, 'X', x_stage)
            c_issued = issue(self.db, 'C', c_stage)
            self.assertEqual(get(self.db, c_issued.record_id).result, actual)
            self.assertEqual(get(self.db, b_issued.record_id).result, b_review.result)
            self.assertEqual(read_preview(self.db, c_review.loan.loan_id), actual)
            self.assertTrue(close(self.db, c_review.loan.loan_id).slot_released)
