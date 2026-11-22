import json

from capture.pages import decode_measurement, encode_measurement, read_measured_page
from process.results import ThicknessResult


def encode_result(result: ThicknessResult) -> str:
    pages = [[page.page_id, encode_measurement(page.thickness_um), page.source]
             for page in result.pages]
    return json.dumps({
        'batch_id': result.batch_id,
        'frozen_id': result.frozen_id,
        'pages': pages,
        'range_hex': encode_measurement(result.range_um),
    }, ensure_ascii=False, separators=(',', ':'))


def decode_result(payload: str) -> ThicknessResult:
    data = json.loads(payload)
    pages = tuple(read_measured_page(page) for page in data['pages'])
    return ThicknessResult(data['batch_id'], data['frozen_id'], pages,
                           decode_measurement(data['range_hex']))
