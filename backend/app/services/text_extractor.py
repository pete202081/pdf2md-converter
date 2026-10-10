"""Per-page text extraction with heading/list/code detection and OCR fallback.

Pages without a text layer (scanned books) are rendered to an image and sent
through the injected :class:`IOcrEngine` — the extractor never depends on a
concrete OCR vendor.
"""

import pymupdf

from app.adapters.ocr.base import IOcrEngine
from app.logging import get_logger
from app.services.pipeline.blocks import (
    CODE,
    HEADING,
    LIST_ITEM,
    PAGE_BREAK,
    PARAGRAPH,
    ContentBlock,
)
from app.services.text_patterns import LIST_PATTERN

_BOLD_FLAG = 16  # PyMuPDF span flag bit for bold
_MONO_FLAG = 8  # PyMuPDF span flag bit for monospaced
_OCR_DPI = 200


class TextExtractor:
    """Extracts positioned content blocks from one page of a PDF."""

    def __init__(self, ocr_engine: IOcrEngine) -> None:
        self._ocr = ocr_engine
        self._logger = get_logger("TextExtractor")

    def extract_page(
        self, document: pymupdf.Document, page_number: int, body_size: float
    ) -> list[ContentBlock]:
        """Return the content blocks of one page (0-based ``page_number``)."""
        page = document[page_number]
        if page.get_text().strip():
            return self._blocks_from_text(page, body_size)
        return self._blocks_from_ocr(page)

    # --- digital pages ------------------------------------------------------
    def _blocks_from_text(self, page: pymupdf.Page, body_size: float) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        paragraph_lines: list[tuple[str, tuple[float, ...]]] = []
        code_lines: list[tuple[str, tuple[float, ...]]] = []

        def flush_paragraph() -> None:
            if paragraph_lines:
                blocks.append(
                    ContentBlock(
                        kind=PARAGRAPH,
                        page=page.number,
                        y=paragraph_lines[0][1][1],
                        text=" ".join(text for text, _ in paragraph_lines),
                        bbox=(
                            min(b[0] for _, b in paragraph_lines),
                            min(b[1] for _, b in paragraph_lines),
                            max(b[2] for _, b in paragraph_lines),
                            max(b[3] for _, b in paragraph_lines),
                        ),
                    )
                )
                paragraph_lines.clear()

        def flush_code() -> None:
            if code_lines:
                blocks.append(
                    ContentBlock(
                        kind=CODE,
                        page=page.number,
                        y=code_lines[0][1][1],
                        text="\n".join(text for text, _ in code_lines),
                        bbox=(
                            min(b[0] for _, b in code_lines),
                            min(b[1] for _, b in code_lines),
                            max(b[2] for _, b in code_lines),
                            max(b[3] for _, b in code_lines),
                        ),
                    )
                )
                code_lines.clear()

        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(span["text"] for span in spans).strip()
                if not text:
                    continue
                bbox = tuple(float(v) for v in line["bbox"])
                y = bbox[1]
                size = max(span["size"] for span in spans)
                bold = any(span["flags"] & _BOLD_FLAG for span in spans)
                mono = any(span["flags"] & _MONO_FLAG for span in spans)

                if mono:
                    flush_paragraph()
                    code_lines.append((text, bbox))
                    continue
                level = self._heading_level(size, bold, body_size)
                if level:
                    flush_paragraph()
                    flush_code()
                    blocks.append(
                        ContentBlock(
                            kind=HEADING, page=page.number, y=y, text=text, level=level, bbox=bbox
                        )
                    )
                    continue
                if LIST_PATTERN.match(text):
                    flush_paragraph()
                    flush_code()
                    blocks.append(
                        ContentBlock(
                            kind=LIST_ITEM,
                            page=page.number,
                            y=y,
                            text=LIST_PATTERN.sub("", text),
                            bbox=bbox,
                        )
                    )
                    continue
                flush_code()
                paragraph_lines.append((text, bbox))
            # PyMuPDF text blocks are distinct layout regions. Keeping them separate
            # lets the table stage suppress only the text inside a detected table.
            flush_paragraph()
            flush_code()

        flush_paragraph()
        flush_code()
        blocks.append(ContentBlock(kind=PAGE_BREAK, page=page.number, y=float("inf")))
        return blocks

    @staticmethod
    def _heading_level(size: float, bold: bool, body_size: float) -> int:
        """Map a span to a Markdown heading level (0 = not a heading)."""
        if size >= body_size * 1.35:
            return 1
        if size >= body_size * 1.15:
            return 2
        if bold and size >= body_size * 1.02:
            return 3
        return 0

    # --- scanned pages (OCR fallback) ---------------------------------------
    def _blocks_from_ocr(self, page: pymupdf.Page) -> list[ContentBlock]:
        page_number = page.number
        # Fail the job if OCR fails; silently dropping a scanned page would
        # produce a misleadingly "successful" but incomplete eBook.
        image_bytes = page.get_pixmap(dpi=_OCR_DPI).tobytes("png")
        text = self._ocr.ocr_image(image_bytes)

        blocks: list[ContentBlock] = []
        y = 0.0
        for raw_paragraph in text.split("\n\n"):
            cleaned = " ".join(raw_paragraph.split())
            if cleaned:
                blocks.append(ContentBlock(kind=PARAGRAPH, page=page_number, y=y, text=cleaned))
                y += 1.0
        blocks.append(ContentBlock(kind=PAGE_BREAK, page=page_number, y=float("inf")))
        return blocks
