"""Chapter domain value objects."""

import re
from dataclasses import dataclass

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_MULTI_UNDERSCORE = re.compile(r"_+")


def slugify(title: str) -> str:
    """Filesystem-safe, readable slug: ``"Getting Started!"`` → ``"Getting_Started"``."""
    slug = _NON_ALNUM.sub("_", title).strip("_")
    slug = _MULTI_UNDERSCORE.sub("_", slug)
    return slug or "Chapter"


@dataclass(slots=True)
class ChapterInfo:
    """One chapter of the source PDF and where its Markdown output goes."""

    order_index: int  # 1-based, defines output order
    title: str
    folder_name: str  # e.g. "01_Introduction" — zero-padded for correct ordering
    start_page: int  # 0-based, inclusive
    end_page: int  # 0-based, inclusive

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page + 1
