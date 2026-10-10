"""Chapter boundary detection.

Strategies, in order of preference (Open/Closed — new strategies plug in here):
1. **PDF outline/bookmarks** — the author's own structure (preferred).
2. **Font-size heading heuristics** — finds large-font / "Chapter N"-style lines.
3. **Single whole-document chapter** — last resort so no content is ever lost.
"""

import re
from collections.abc import Sequence

import pymupdf

from app.domain.chapters import ChapterInfo, slugify
from app.logging import get_logger
from app.services.pdf_stats import estimate_body_size

logger = get_logger("ChapterDetector")

_HEADING_PATTERN = re.compile(r"^\s*(chapter|part|section|appendix)\s+\w+", re.IGNORECASE)
_HEADING_SIZE_FACTOR = 1.15  # a line at least 15% larger than body text
_MAX_HEADING_LENGTH = 100
_FRONT_MATTER_TITLE = "Front Matter"
_FULL_DOCUMENT_TITLE = "Full Document"


class ChapterDetector:
    """Detects chapter boundaries of a PDF document."""

    def detect(self, document: pymupdf.Document) -> list[ChapterInfo]:
        """Return the ordered list of chapters (always at least one)."""
        page_count = document.page_count

        toc = document.get_toc()
        if toc:
            chapters = self._from_toc(toc, page_count)
            if chapters:
                logger.info("chapters_detected", source="toc", count=len(chapters))
                return self._normalize_folders(chapters)

        chapters = self._from_headings(document, page_count)
        if chapters:
            logger.info("chapters_detected", source="heuristics", count=len(chapters))
            return self._normalize_folders(chapters)

        logger.warning("chapters_detected", source="fallback", count=1)
        return [self._make_chapter(1, _FULL_DOCUMENT_TITLE, 0, page_count - 1)]

    # --- strategy 1: PDF outline -----------------------------------------
    def _from_toc(self, toc: Sequence[Sequence], page_count: int) -> list[ChapterInfo]:
        min_level = min(entry[0] for entry in toc)
        entries = [entry for entry in toc if entry[0] == min_level] or list(toc)
        # get_toc() pages are 1-based. Discard invalid bookmarks and group
        # same-page bookmarks so a page is never emitted twice or skipped.
        starts_by_page: dict[int, str] = {}
        for entry in entries:
            title = str(entry[1]).strip()
            page = int(entry[2])
            if title and 1 <= page <= page_count:
                starts_by_page.setdefault(page - 1, title)
        if not starts_by_page:
            return []
        starts = [(title, page) for page, title in sorted(starts_by_page.items())]
        if starts[0][1] > 0:
            starts.insert(0, (_FRONT_MATTER_TITLE, 0))
        chapters = []
        for index, (title, start) in enumerate(starts):
            end = starts[index + 1][1] - 1 if index + 1 < len(starts) else page_count - 1
            chapters.append(self._make_chapter(index + 1, title, start, end))
        return chapters

    # --- strategy 2: heading heuristics -----------------------------------
    def _from_headings(self, document: pymupdf.Document, page_count: int) -> list[ChapterInfo]:
        body_size = estimate_body_size(document, sample_pages=50)
        threshold = body_size * _HEADING_SIZE_FACTOR

        candidates: list[tuple[str, int]] = []  # (title, 0-based page)
        for page_number in range(page_count):
            title = self._find_heading(document[page_number], threshold)
            if title:
                candidates.append((title, page_number))
        if not candidates:
            return []

        # Content before the first detected heading still belongs to the book
        if candidates[0][1] > 0:
            candidates.insert(0, (_FRONT_MATTER_TITLE, 0))

        chapters = []
        for index, (title, start) in enumerate(candidates):
            end = candidates[index + 1][1] - 1 if index + 1 < len(candidates) else page_count - 1
            chapters.append(self._make_chapter(index + 1, title, start, max(start, end)))
        return chapters

    @staticmethod
    def _find_heading(page: pymupdf.Page, threshold: float) -> str | None:
        """Topmost line that is notably larger than body text or matches 'Chapter N'."""
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(span["text"] for span in spans).strip()
                if not text or len(text) > _MAX_HEADING_LENGTH:
                    continue
                size = max(span["size"] for span in spans)
                if size >= threshold or _HEADING_PATTERN.match(text):
                    return text
        return None

    # --- helpers -----------------------------------------------------------
    @staticmethod
    def _normalize_folders(chapters: list[ChapterInfo]) -> list[ChapterInfo]:
        """Width follows chapter count (three digits for 100+ chapters)."""
        width = max(2, len(str(len(chapters))))
        for chapter in chapters:
            chapter.folder_name = f"{chapter.order_index:0{width}d}_{slugify(chapter.title)}"
        return chapters

    @staticmethod
    def _make_chapter(order: int, title: str, start: int, end: int) -> ChapterInfo:
        clean_title = " ".join(title.split()) or f"Chapter {order}"
        return ChapterInfo(
            order_index=order,
            title=clean_title,
            folder_name=f"{order:02d}_{slugify(clean_title)}",
            start_page=start,
            end_page=end,
        )
