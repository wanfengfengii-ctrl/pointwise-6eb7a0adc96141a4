import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from archive.database import StationError, open_station_database
from archive.retrieve import get
from capture.batches import register
from capture.freeze import receive
from issue.commit import issue
from issue.order import current_frontier
from process.slots import list_slots
from process.staging import stage


@contextmanager
def deny_commit(db):
    def authorize(action, argument, *_):
        if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(authorize)
    try:
        yield
    finally:
        db.set_authorizer(None)


def rejected_issue(db, batch, staged_id):
    try:
        issue(db, batch, staged_id)
    except StationError as error:
        return error.code
    raise AssertionError(f"{batch} unexpectedly signed")


def result_fields(result):
    return {
        "batch_id": result.batch_id,
        "pages": [[page.page_id, page.thickness_um, page.source] for page in result.pages],
        "range_um": result.range_um,
        "missing_count": result.missing_count,
    }


def check_fixture(sample):
    expected = sample["expected_v1"]
    first_a, late_a = sample["arrivals"]["A"]
    (page_b,) = sample["arrivals"]["B"]
    with closing(open_station_database(":memory:")) as db:
        for batch, pages in sample["registrations"]:
            register(db, batch, pages)
        first = receive(db, "A", *first_a)
        assert first.frozen_id is None
        assert list(first.coverage.missing) == expected["missing_before"]
        b = stage(db, receive(db, "B", *page_b).frozen_id).staged_id
        later_error = rejected_issue(db, "B", b)
        assert later_error == expected["issue_error"]

        register(db, "A-copy", [first_a[0]])
        copy = stage(db, receive(db, "A-copy", *first_a).frozen_id).staged_id
        copy_error = rejected_issue(db, "A-copy", copy)
        assert copy_error == expected["issue_error"]

        full_a = receive(db, "A", *late_a).frozen_id
        staged_a = stage(db, full_a).staged_id
        slots = list_slots(db)
        try:
            with deny_commit(db):
                issue(db, "A", staged_a)
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("the commit veto did not fire")
        assert not db.in_transaction
        assert current_frontier(db) == 0
        assert list_slots(db) == slots
        for table in ("archive_index", "archive_records", "issue_receipts"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
        issued = issue(db, "A", staged_a)
        stored = get(db, issued.record_id)
        assert stored.receipt == issued.receipt
        whole = result_fields(stored.result)
        assert whole == expected["A"]
    return {"B_issue": later_error, "appended_A_copy_issue": copy_error, "whole_A": whole}


def main():
    sample_path = Path(__file__).resolve().parent / "samples" / "split_shift.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    print("SPLIT_BASELINE " + json.dumps(check_fixture(sample), ensure_ascii=False))


if __name__ == "__main__":
    main()
