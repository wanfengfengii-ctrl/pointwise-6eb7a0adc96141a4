import sqlite3

from archive.retrieve import get
from capture.batches import register
from capture.freeze import receive, split, split_status
from check_split_baseline import deny_commit
from issue.commit import issue, issue_part
from issue.order import current_frontier
from process.slots import list_slots
from process.staging import stage, stage_part
from review.admission import preview
from review.closing import close
from review.readout import read_preview

from tests.fixtures import StationCase


def _pages(result):
    return [(page.page_id, page.thickness_um, page.source) for page in result.pages]


class SplitShiftTests(StationCase):
    def register_split_shift(self):
        register(self.db, 'A', ['1', '2'])
        register(self.db, 'B', ['1'])
        receive(self.db, 'A', '1', 'scan/A/1', 7)
        receive(self.db, 'B', '1', 'scan/B/1', 11)
        b_stage = stage(self.db, 2).staged_id
        return b_stage

    def test_split_eligibility_and_boundaries(self):
        self.register_split_shift()
        # unknown batch
        with self.error('BATCH_NOT_FOUND'):
            split(self.db, 'Z', '1')
        # B is a later batch: not the earliest unissued
        with self.error('OUT_OF_ORDER'):
            split(self.db, 'B', '1')
        # missing and unknown pages
        with self.error('INVALID_SPLIT_BOUNDARY'):
            split(self.db, 'A', '2')
        with self.error('FOREIGN_PAGE'):
            split(self.db, 'A', '9')
        blank = register(self.db, 'C', ['1'])
        # a complete later batch is rejected on ordering first
        receive(self.db, 'C', '1', 'scan/C/1', 1)
        with self.error('OUT_OF_ORDER'):
            split(self.db, 'C', '1')
        front, back = split(self.db, 'A', '1')
        self.assertEqual((front, back), (1, 2))
        status = split_status(self.db, 'A')
        self.assertEqual(status[0].part_id, front)
        self.assertEqual(status[1].part_id, back)
        self.assertEqual(status[0].missing, ())
        self.assertEqual(status[1].missing, ('2',))
        self.assertIsNotNone(status[0].frozen_id)
        self.assertIsNone(status[1].frozen_id)

    def test_complete_head_cannot_split(self):
        register(self.db, 'H', ['1', '2'])
        receive(self.db, 'H', '1', 'scan/H/1', 1)
        receive(self.db, 'H', '2', 'scan/H/2', 2)
        with self.error('BATCH_NOT_READY'):
            split(self.db, 'H', '1')

    def test_same_boundary_replays_other_boundary_rejected(self):
        self.register_split_shift()
        first = split(self.db, 'A', '1')
        # identical boundary in any later state returns the original part ids
        self.assertEqual(split(self.db, 'A', '1'), first)
        with self.error('SPLIT_BOUNDARY_CONFLICT'):
            split(self.db, 'A', '2')
        # still the same parts after the rejected attempt
        self.assertEqual(split(self.db, 'A', '1'), first)
        with self.error('BATCH_NOT_SPLIT'):
            split_status(self.db, 'B')
        with self.error('BATCH_NOT_FOUND'):
            split_status(self.db, 'Z')

    def test_pages_continue_at_parent_address_with_dedup(self):
        self.register_split_shift()
        front, back = split(self.db, 'A', '1')
        # replay before any new arrival
        replay = receive(self.db, 'A', '1', 'scan/A/1', 7)
        self.assertTrue(replay.replayed)
        self.assertIsNone(replay.frozen_id)
        self.assertEqual(replay.coverage.missing, ('2',))
        # changed value or source is rejected
        with self.error('PAGE_CONFLICT'):
            receive(self.db, 'A', '1', 'scan/A/1', 8)
        with self.error('PAGE_CONFLICT'):
            receive(self.db, 'A', '1', 'scan/A/other', 7)
        # frozen page content matches the pre-split received page
        staged = stage_part(self.db, front).staged_id
        self.assertEqual(_pages(preview(self.db, 'qc', staged).result),
                         [('1', 7, 'scan/A/1')])
        # late page completes only the back segment
        late = receive(self.db, 'A', '2', 'scan/A/2', 20)
        self.assertIsNone(late.frozen_id)
        status = split_status(self.db, 'A')
        self.assertEqual(status[0].missing, ())
        self.assertEqual(status[1].missing, ())
        self.assertIsNotNone(status[1].frozen_id)

    def test_front_issues_while_back_missing_and_blocks_later_batch(self):
        b_stage = self.register_split_shift()
        front, back = split(self.db, 'A', '1')
        front_stage = stage_part(self.db, front)
        front_issue = issue_part(self.db, front, front_stage.staged_id)
        # frontier has not crossed A yet
        self.assertEqual(current_frontier(self.db), 0)
        archived_front = get(self.db, front_issue.record_id)
        self.assertEqual(archived_front.part_id, front)
        self.assertEqual(archived_front.result.batch_id, 'A')
        self.assertEqual(_pages(archived_front.result), [('1', 7, 'scan/A/1')])
        self.assertEqual(archived_front.result.range_um, 0)
        self.assertEqual(archived_front.result.missing_count, 0)
        # B remains out of order while the back segment is still pending
        with self.error('OUT_OF_ORDER'):
            issue(self.db, 'B', b_stage)
        # parent cannot be issued as a whole batch
        with self.error('BATCH_SPLIT'):
            issue(self.db, 'A', front_stage.staged_id)
        # same part cannot be issued twice
        with self.error('ALREADY_ISSUED'):
            issue_part(self.db, front, front_stage.staged_id)
        # back cannot stage before it is complete
        with self.error('PART_NOT_READY'):
            stage_part(self.db, back)

    def test_full_handover_order_front_back_b(self):
        b_stage = self.register_split_shift()
        front, back = split(self.db, 'A', '1')
        front_issue = issue_part(self.db, front, stage_part(self.db, front).staged_id)
        receive(self.db, 'A', '2', 'scan/A/2', 20)
        back_stage = stage_part(self.db, back)
        back_issue = issue_part(self.db, back, back_stage.staged_id)
        self.assertEqual(current_frontier(self.db), 1)
        # back archive only carries its own pages
        archived_back = get(self.db, back_issue.record_id)
        self.assertEqual(archived_back.part_id, back)
        self.assertEqual(_pages(archived_back.result), [('2', 20, 'scan/A/2')])
        self.assertEqual(archived_back.result.range_um, 0)
        self.assertEqual(archived_back.result.missing_count, 0)
        # now B can sign using its original staged id
        b_issue = issue(self.db, 'B', b_stage)
        self.assertEqual(current_frontier(self.db), 2)
        archived_b = get(self.db, b_issue.record_id)
        self.assertIsNone(archived_b.part_id)
        self.assertEqual(_pages(archived_b.result), [('1', 11, 'scan/B/1')])
        # independent record ids and receipts, in issue order
        ids = [front_issue.record_id, back_issue.record_id, b_issue.record_id]
        self.assertEqual(ids, sorted(set(ids)))
        receipts = [front_issue.receipt, back_issue.receipt, b_issue.receipt]
        self.assertEqual(len({receipt.receipt_id for receipt in receipts}), 3)

    def test_reopen_retrieves_part_identity_and_first_execution(self):
        b_stage = self.register_split_shift()
        front, back = split(self.db, 'A', '1')
        front_stage = stage_part(self.db, front).staged_id
        front_issue = issue_part(self.db, front, front_stage)
        receive(self.db, 'A', '2', 'scan/A/2', 20)
        back_stage = stage_part(self.db, back).staged_id
        back_issue = issue_part(self.db, back, back_stage)
        b_issue = issue(self.db, 'B', b_stage)
        self.db.close()
        reopened = self.open_station()
        for issued, part_id in (
            (front_issue, front),
            (back_issue, back),
            (b_issue, None),
        ):
            archived = get(reopened, issued.record_id)
            self.assertEqual(archived.receipt, issued.receipt)
            self.assertEqual(archived.part_id, part_id)
            self.assertEqual(archived.receipt.staged_id, issued.receipt.staged_id)
        # split status still queryable after reopen, both segments complete
        status = split_status(reopened, 'A')
        self.assertEqual(tuple(item.missing for item in status), ((), ()))
        self.assertIsNotNone(status[0].frozen_id)
        self.assertIsNotNone(status[1].frozen_id)

    def test_full_slot_reclaim_keeps_borrowed_front_preview(self):
        b_stage = self.register_split_shift()
        front, back = split(self.db, 'A', '1')
        front_staged = stage_part(self.db, front).staged_id
        loan_id = preview(self.db, 'qc', front_staged).loan.loan_id
        issue_part(self.db, front, front_staged)
        # both slots occupied: signed+borrowed front, waiting B
        self.assertEqual(len([slot for slot in list_slots(self.db) if slot.staged_id]), 2)
        receive(self.db, 'A', '2', 'scan/A/2', 20)
        back_stage = stage_part(self.db, back)
        # head unit reclaims B's slot; the borrowed front slot is untouched
        self.assertEqual(back_stage.evicted_id, b_stage)
        self.assertEqual(_pages(read_preview(self.db, loan_id)),
                         [('1', 7, 'scan/A/1')])
        # the original B staged id is now dead
        with self.error('OUT_OF_ORDER'):
            issue(self.db, 'B', b_stage)
        issue_part(self.db, back, back_stage.staged_id)
        # B recomputes from its original whole-batch frozen id and signs
        b_recomputed = stage(self.db, 2)
        self.assertNotEqual(b_recomputed.staged_id, b_stage)
        b_issue = issue(self.db, 'B', b_recomputed.staged_id)
        self.assertEqual(_pages(get(self.db, b_issue.record_id).result),
                         [('1', 11, 'scan/B/1')])
        self.assertTrue(close(self.db, loan_id).slot_released)

    def test_back_issue_commit_failure_leaves_no_half_booking(self):
        b_stage = self.register_split_shift()
        front, back = split(self.db, 'A', '1')
        front_staged = stage_part(self.db, front).staged_id
        loan_id = preview(self.db, 'qc', front_staged).loan.loan_id
        front_issue = issue_part(self.db, front, front_staged)
        receive(self.db, 'A', '2', 'scan/A/2', 20)
        back_stage = stage_part(self.db, back)
        slots_before = list_slots(self.db)
        try:
            with deny_commit(self.db):
                issue_part(self.db, back, back_stage.staged_id)
        except sqlite3.DatabaseError:
            pass
        else:
            self.fail('commit veto did not fire')
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(current_frontier(self.db), 0)
        self.assertEqual(list_slots(self.db), slots_before)
        # front first execution and its open preview survive
        self.assertEqual(get(self.db, front_issue.record_id).part_id, front)
        self.assertEqual(_pages(read_preview(self.db, loan_id)), [('1', 7, 'scan/A/1')])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM archive_index').fetchone(), (1,))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM issue_receipts').fetchone(), (1,))
        # B still cannot pass; back still staged, retry works
        with self.error('OUT_OF_ORDER'):
            issue(self.db, 'B', b_stage)
        back_issue = issue_part(self.db, back, back_stage.staged_id)
        self.assertEqual(current_frontier(self.db), 1)
        b_recomputed = stage(self.db, 2)
        b_issue = issue(self.db, 'B', b_recomputed.staged_id)
        self.assertEqual(get(self.db, back_issue.record_id).part_id, back)
        self.assertIsNone(get(self.db, b_issue.record_id).part_id)

    def test_part_stage_reuse_and_mismatched_staged_id(self):
        register(self.db, 'A', ['1', '2'])
        register(self.db, 'B', ['1'])
        receive(self.db, 'A', '1', 'scan/A/1', 7)
        front, back = split(self.db, 'A', '1')
        first = stage_part(self.db, front)
        reused = stage_part(self.db, front)
        self.assertTrue(reused.reused)
        self.assertEqual(reused.staged_id, first.staged_id)
        receive(self.db, 'A', '2', 'scan/A/2', 20)
        back_stage = stage_part(self.db, back)
        # using the back segment's staged id for the front part is a mismatch
        with self.error('STAGE_BATCH_MISMATCH'):
            issue_part(self.db, front, back_stage.staged_id)
        # unknown part id
        with self.error('PART_NOT_FOUND'):
            stage_part(self.db, 999)

    def test_whole_batch_apis_reject_split_parent(self):
        self.register_split_shift()
        front, _ = split(self.db, 'A', '1')
        # whole-batch staging of the parent position is refused
        with self.error('BATCH_SPLIT'):
            stage(self.db, 1)
        staged = stage_part(self.db, front).staged_id
        with self.error('BATCH_SPLIT'):
            issue(self.db, 'A', staged)

    def test_missing_named_pages_count_per_segment(self):
        register(self.db, 'D', ['1', '2', '3'])
        register(self.db, 'E', ['1'])
        receive(self.db, 'D', '1', 'scan/D/1', 7)
        receive(self.db, 'D', '2', 'scan/D/2', None)
        front, back = split(self.db, 'D', '2')
        status = split_status(self.db, 'D')
        self.assertEqual(status[0].missing, ())
        self.assertEqual(status[1].missing, ('3',))
        front_issue = issue_part(self.db, front, stage_part(self.db, front).staged_id)
        result = get(self.db, front_issue.record_id).result
        self.assertEqual(result.range_um, 0)
        self.assertEqual(result.missing_count, 1)
        # named-missing page still counts as received
        replay = receive(self.db, 'D', '2', 'scan/D/2', None)
        self.assertTrue(replay.replayed)

    def test_persisted_split_keeps_other_batch_identity(self):
        # B staged before the split keeps its registration, frozen id and slot
        b_stage = self.register_split_shift()
        front, back = split(self.db, 'A', '1')
        slots = list_slots(self.db)
        self.assertIn(b_stage, [slot.staged_id for slot in slots])
        issue_part(self.db, front, stage_part(self.db, front).staged_id)
        # initialize again: all state preserved
        from archive.database import initialize_station
        initialize_station(self.db)
        self.assertEqual(split(self.db, 'A', '1'), (front, back))
        self.assertIn(b_stage, [slot.staged_id for slot in list_slots(self.db)])
