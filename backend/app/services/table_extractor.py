"""Table extraction: PDF tables → Markdown tables (via pdfplumber)."""

from pdfplumber.page import Page

from app.logging import get_logger
from app.services.pipeline.blocks import TABLE, ContentBlock

logger = get_logger("TableExtractor")


def render_markdown_table(rows: list[list[str | None]]) -> str | None:
    """Render extracted table rows as a GitHub-flavored Markdown table.

    Returns ``None`` when the table is empty or has no header row.
    """
    cleaned = [
        [(cell or "").replace("|", "\\|").replace("\n", " ").strip() for cell in row]
        for row in rows
    ]
    cleaned = [row for row in cleaned if any(row)]
    if len(cleaned) < 2 or not cleaned[0]:
        return None
    width = len(cleaned[0])
    normalized = [row + [""] * (width - len(row)) for row in cleaned]
    lines = [
        "| " + " | ".join(normalized[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


class TableExtractor:
    """Extracts positioned Markdown-table blocks from one PDF page."""

    def extract_page(self, page: Page, page_number: int) -> list[ContentBlock]:
        """Return TABLE blocks found on ``page`` (0-based ``page_number``)."""
        try:
            tables = page.find_tables()
        except Exception:
            logger.exception("table_detection_failed", page=page_number)
            return []
        blocks: list[ContentBlock] = []
        for table in tables:
            markdown = render_markdown_table(table.extract())
            if not markdown:
                continue
            y = float(table.bbox[1]) if table.bbox else 0.0
            blocks.append(
                ContentBlock(
                    kind=TABLE,
                    page=page_number,
                    y=y,
                    text=markdown,
                    bbox=tuple(float(value) for value in table.bbox),
                )
            )
        return blocks
