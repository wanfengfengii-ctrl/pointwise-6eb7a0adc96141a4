from collections.abc import Iterable
from dataclasses import dataclass

from capture.manifest import ExpectedPages
from capture.pages import MeasuredPage


@dataclass(frozen=True)
class PageCoverage:
    received: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing


def compute_coverage(manifest: ExpectedPages, originals: Iterable[MeasuredPage]) -> PageCoverage:
    received_ids = {original.page_id for original in originals}
    received = tuple(page for page in manifest.page_ids if page in received_ids)
    missing = tuple(page for page in manifest.page_ids if page not in received_ids)
    return PageCoverage(received, missing)
