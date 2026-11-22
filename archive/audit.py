from archive.database import StationError
from archive.index import RecordKey
from issue.receipts import IssueReceipt
from process.results import ThicknessResult


def check_record_binding(key: RecordKey, result: ThicknessResult, receipt: IssueReceipt) -> None:
    if (result.batch_id, result.frozen_id) != (key.batch_id, key.frozen_id):
        raise StationError('ARCHIVE_BINDING_MISMATCH', f'payload for record {key.record_id}')
    if (receipt.record_id, receipt.batch_id, receipt.staged_id) != (
            key.record_id, key.batch_id, key.staged_id):
        raise StationError('ARCHIVE_BINDING_MISMATCH', f'receipt for record {key.record_id}')
