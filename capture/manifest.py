from collections.abc import Sequence
from dataclasses import dataclass

from archive.database import StationError


def check_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise StationError(code, 'expected nonblank text')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise StationError(code, 'expected UTF-8 text') from error
    return value


@dataclass(frozen=True)
class ExpectedPages:
    page_ids: tuple[str, ...]

    def contains(self, page_id: str) -> bool:
        return page_id in self.page_ids


def declare_pages(pages: Sequence[str]) -> ExpectedPages:
    if isinstance(pages, (str, bytes, bytearray)) or not isinstance(pages, Sequence):
        raise StationError('INVALID_MANIFEST', 'expected an ordered sequence of page names')
    page_ids = tuple(check_text(page, 'INVALID_PAGE_ID') for page in pages)
    if not page_ids or len(set(page_ids)) != len(page_ids):
        raise StationError('INVALID_MANIFEST', 'page names must be nonempty and unique')
    return ExpectedPages(page_ids)
