from archive.retrieve import get
from capture.batches import find_registration, register
from capture.freeze import load_frozen_batch, receive
from capture.pages import MeasuredPage, read_received_pages
from issue.commit import issue
from process.staging import stage
from review.admission import preview

from tests.fixtures import StationCase


class CaptureTests(StationCase):
    def test_out_of_order_pages(self):
        batch = register(self.db, 'B', ['1', '2'])
        first = receive(self.db, 'B', '2', 'scan/B/2', 11)
        self.assertEqual(first.coverage.received, ('2',))
        self.assertEqual(first.coverage.missing, ('1',))
        self.assertIsNone(first.frozen_id)
        last = receive(self.db, 'B', '1', 'scan/B/1', 7)
        self.assertEqual(last.frozen_id, batch.position)
        self.assertEqual(last.coverage.received, ('1', '2'))
        self.assertEqual(last.coverage.missing, ())
        staged = stage(self.db, last.frozen_id)
        result = preview(self.db, 'qc', staged.staged_id).result
        self.assert_result(result, batch='B', pages=[('1', 7, 'scan/B/1'), ('2', 11, 'scan/B/2')],
                           spread=4, missing=0)

    def test_registration_retry(self):
        pages = ['tail', 'head']
        first = register(self.db, 'A', pages)
        pages.reverse()
        self.assertEqual(register(self.db, 'A', ['tail', 'head']), first)
        with self.error('REGISTRATION_CONFLICT'):
            register(self.db, 'A', pages)
        self.assertEqual(find_registration(self.db, 'A'), first)
        for manifest in ([], ['one', 'one'], 'one'):
            with self.subTest(manifest=manifest), self.error('INVALID_MANIFEST'):
                register(self.db, 'bad', manifest)
        later = register(self.db, 'B', ['p'])
        self.assertEqual(later.position, first.position + 1)
        frozen_id = self.receive_all('A', [('head', 'head-scan', 11), ('tail', 'tail-scan', 7)])
        frozen = load_frozen_batch(self.db, frozen_id)
        self.assertEqual([page.page_id for page in frozen.pages], ['tail', 'head'])

    def test_freeze_replay(self):
        registered = register(self.db, 'A', ['1', '2'])
        receive(self.db, 'A', '2', 'scan/A/2', None)
        frozen = receive(self.db, 'A', '1', 'scan/A/1', 7)
        repeated = receive(self.db, 'A', '2', 'scan/A/2', None)
        self.assertTrue(repeated.replayed)
        self.assertEqual(repeated.frozen_id, frozen.frozen_id)
        self.assertEqual(repeated.coverage, frozen.coverage)
        for source, value in [('scan/A/2', 0), ('different source', None)]:
            with self.subTest(source=source, value=value), self.error('PAGE_CONFLICT'):
                receive(self.db, 'A', '2', source, value)
        self.assertEqual(load_frozen_batch(self.db, registered.position).pages,
                         (MeasuredPage('1', 7, 'scan/A/1'), MeasuredPage('2', None, 'scan/A/2')))

    def test_missing_is_not_zero(self):
        inputs = [(('m', None, 'second'), ('h', 11, 'third')),
                  (('m', None, 'second'), ('h', None, 'third'))]
        for index, (other_pages, expected_range, missing) in enumerate(zip(inputs, (4, 0), (1, 2)), 1):
            with self.subTest(spread=expected_range):
                batch = f'B{index}'
                register(self.db, batch, ['l', 'm', 'h'])
                pages = [('l', 7, 'first'), *other_pages]
                frozen_id = self.receive_all(batch, [(page, source, value) for page, value, source in pages])
                issued = issue(self.db, batch, stage(self.db, frozen_id).staged_id)
                actual = get(self.db, issued.record_id).result
                self.assert_result(actual, batch=batch, pages=pages, spread=expected_range, missing=missing)

    def test_foreign_page_rejected(self):
        batch = register(self.db, 'A', ['1', '2'])
        receive(self.db, 'A', '1', 'scan/A/1', 9)
        with self.error('FOREIGN_PAGE'):
            receive(self.db, 'A', '9', 'wrong/9', 20)
        replay = receive(self.db, 'A', '1', 'scan/A/1', 9)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.coverage.missing, ('2',))
        self.assertIsNone(replay.frozen_id)
        self.assertEqual(read_received_pages(self.db, batch.position), (MeasuredPage('1', 9, 'scan/A/1'),))
        with self.error('BATCH_NOT_READY'):
            stage(self.db, batch.position)
        final = receive(self.db, 'A', '2', 'scan/A/2', None)
        self.assertEqual(final.frozen_id, batch.position)
        self.assertEqual(load_frozen_batch(self.db, final.frozen_id).pages,
                         (MeasuredPage('1', 9, 'scan/A/1'), MeasuredPage('2', None, 'scan/A/2')))

    def test_invalid_originals_do_not_complete_a_batch(self):
        register(self.db, 'A', ['x', 'y'])
        for unsupported in (True, 7.0, '7'):
            with self.subTest(value=unsupported), self.error('INVALID_THICKNESS'):
                receive(self.db, 'A', 'y', 'scan/y', unsupported)
        with self.error('INVALID_SOURCE'):
            receive(self.db, 'A', 'y', ' ', None)
        actual = receive(self.db, 'A', 'x', 'scan/x', 7)
        self.assertEqual(actual.coverage.missing, ('y',))
        self.assertIsNone(actual.frozen_id)
