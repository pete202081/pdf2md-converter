"""Content blocks: the intermediate representation between extraction and assembly.

Extraction stages (text, tables, images, …) each produce a flat list of
:class:`ContentBlock`. The assembler merges them per chapter, sorts by
``(page, y)`` and renders the final Markdown. New content kinds (e.g.
``mermaid`` in Phase 3) plug in without touching the extraction stages.
"""

from dataclasses import dataclass

# Block kinds
HEADING = "heading"
PARAGRAPH = "paragraph"
LIST_ITEM = "list_item"
CODE = "code"
TABLE = "table"
IMAGE = "image"
MERMAID = "mermaid"  # produced by the Phase-3 diagram conversion stage
PAGE_BREAK = "page_break"


@dataclass(slots=True)
class ContentBlock:
    """A positioned piece of chapter content."""

    kind: str
    page: int  # 0-based page number in the source PDF
    y: float  # vertical position on the page (top edge) — used for ordering
    text: str = ""
    level: int = 0  # heading level (1–6), only for HEADING blocks
    image_path: str = ""  # output-relative path, only for IMAGE blocks
    alt: str = ""  # alt text, only for IMAGE blocks
    bbox: tuple[float, ...] | None = None  # PDF coordinates, for table deduplication
    review_id: str = ""  # stable anchor for a fallback diagram occurrence
