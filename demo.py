import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.database import StationError, open_station_database
from archive.retrieve import get
from capture.batches import register
from capture.freeze import receive
from issue.commit import issue
from process.staging import stage
from review.admission import preview
from review.closing import close
from review.readout import read_preview


SAMPLES = Path(__file__).resolve().parent / 'samples'


def read_sample():
    sample = json.loads((SAMPLES / 'shift.json').read_text(encoding='utf-8'))
    expected = json.loads((SAMPLES / 'expected.json').read_text(encoding='utf-8'))
    return sample, expected


def _as_json(result):
    return {
        'pages': [[page.page_id, page.thickness_um, page.source] for page in result.pages],
        'range_um': result.range_um,
        'missing_count': result.missing_count,
    }


def _require_equal(actual, expected, description):
    if actual != expected:
        raise AssertionError(f'{description}: {actual!r} != {expected!r}')


def run_demo(path, sample, expected):
    arrivals = sample['arrivals']
    with closing(open_station_database(path)) as db:
        for batch, manifest in sample['registrations'].items():
            register(db, batch, manifest)
        pending = receive(db, 'A', *arrivals['A'][0])
        _require_equal(pending.coverage.missing, ('2',), 'A waits for page 2')
        receive(db, 'B', *arrivals['B'][0])
        b = receive(db, 'B', *arrivals['B'][1])
        b_stage = stage(db, b.frozen_id).staged_id
        b_preview = preview(db, 'qc', b_stage)
        loan = b_preview.loan.loan_id
        receive(db, 'C', *arrivals['C'][0])
        c = receive(db, 'C', *arrivals['C'][1])
        try:
            c_first = stage(db, c.frozen_id).staged_id
        except StationError as error:
            if error.code != 'NO_SLOT':
                raise
            c_first = None
        a = receive(db, 'A', *arrivals['A'][1])
        a_stage = stage(db, a.frozen_id)
        if c_first is not None:
            _require_equal(a_stage.evicted_id, c_first, 'C yields the occupied slot')
        b_during = _as_json(read_preview(db, loan))

        a_issued = issue(db, 'A', a_stage.staged_id)
        b_issued = issue(db, 'B', b_stage)
        b_after_issue = _as_json(read_preview(db, loan))
        _require_equal(close(db, loan).slot_released, True, 'B returns its slot')
        c_stage = stage(db, c.frozen_id).staged_id
        c_preview = preview(db, 'qc', c_stage)
        _require_equal(close(db, loan).closed_now, False, 'old close is a replay')
        c_after_replay = _as_json(read_preview(db, c_preview.loan.loan_id))
        c_issued = issue(db, 'C', c_stage)
        close(db, c_preview.loan.loan_id)

    actual = {
        'B_preliminary': _as_json(b_preview.result),
        'B_during_A_staging': b_during,
        'B_after_issue': b_after_issue,
        'C_after_old_close': c_after_replay,
    }
    preview_expected = {
        'B_preliminary': expected['B'],
        'B_during_A_staging': expected['B'],
        'B_after_issue': expected['B'],
        'C_after_old_close': expected['C'],
    }
    downstream = {}
    record_ids = []
    with closing(open_station_database(path)) as db:
        for batch, issued in zip(('A', 'B', 'C'), (a_issued, b_issued, c_issued), strict=True):
            archived = get(db, issued.record_id)
            _require_equal(archived.result.batch_id, batch, 'archived batch')
            _require_equal(archived.receipt, issued.receipt, 'original receipt')
            record_ids.append(archived.record_id)
            downstream[batch] = _as_json(archived.result)
    _require_equal(record_ids, sorted(set(record_ids)), 'one record per batch in registration order')
    _require_equal(actual, preview_expected, 'preview content')
    _require_equal(downstream, expected, 'reopened archive values')
    return {
        'case_id': 'v5',
        'input': sample,
        'expected': preview_expected,
        'actual': actual,
        'downstream_expected': expected,
        'downstream_actual': downstream,
        'code_paths': ['capture/freeze.py', 'process/planning.py', 'process/staging.py',
                       'review/readout.py', 'review/closing.py', 'issue/commit.py',
                       'archive/retrieve.py'],
        'oracle': 'samples/expected.json lists pages in registered order and hand-calculated ranges 0, 4, 4',
    }


def main():
    sample, expected = read_sample()
    with TemporaryDirectory(prefix='beiting-demo-') as directory:
        evidence = run_demo(Path(directory) / 'station.sqlite', sample, expected)
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
