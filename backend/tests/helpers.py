"""Shared test helpers: pub/sub utilities and synthetic PDF builders."""

import io
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw


async def next_pubsub_message(
    pubsub: Any, timeout: float = 1.0, attempts: int = 5
) -> dict[str, Any] | None:
    """Return the next real published message, skipping subscribe confirmations.

    Works around a fakeredis quirk where the first ``get_message()`` call after
    subscribing returns ``None`` even when a message is already queued (real
    Redis returns the queued message directly).
    """
    for _ in range(attempts):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
        if message is not None and message.get("type") == "message":
            return message
    return None


def make_png(path: Path, width: int = 100, height: int = 80) -> str:
    """Create a small valid PNG file (used to embed images into test PDFs)."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    path.write_bytes(pixmap.tobytes("png"))
    return str(path)


def draw_table(
    page: pymupdf.Page,
    cells: list[list[str]],
    x0: float = 72,
    y0: float = 150,
    width: float = 300,
    height: float = 90,
) -> None:
    """Draw a real grid table (lines + cell text) that pdfplumber can detect."""
    rows, cols = len(cells), len(cells[0])
    for i in range(cols + 1):
        x = x0 + i * width / cols
        page.draw_line((x, y0), (x, y0 + height))
    for j in range(rows + 1):
        y = y0 + j * height / rows
        page.draw_line((x0, y), (x0 + width, y))
    for j, row in enumerate(cells):
        for i, cell in enumerate(row):
            page.insert_text(
                (x0 + i * width / cols + 5, y0 + j * height / rows + height / rows * 0.7),
                cell,
                fontsize=10,
            )


def build_book_pdf(path: Path) -> str:
    """Build a 4-page test book: 2 chapters (via TOC), a table, an image, a scanned page.

    Page layout (0-based):
    - 0: "Introduction" heading + body          → chapter 1
    - 1: "Getting Started" heading + body + table → chapter 2
    - 2: an embedded image + caption           → chapter 2
    - 3: blank (scanned page → OCR fallback)    → chapter 2
    """
    document = pymupdf.open()

    page = document.new_page()
    page.insert_text((72, 72), "Introduction", fontsize=20)
    page.insert_text((72, 100), "Welcome to the book body text.", fontsize=11)

    page = document.new_page()
    page.insert_text((72, 72), "Getting Started", fontsize=20)
    page.insert_text((72, 100), "First steps body text.", fontsize=11)
    draw_table(
        page,
        [["Name", "Age", "City"], ["Alice", "30", "Paris"], ["Bob", "25", "Lyon"]],
    )

    page = document.new_page()
    image_path = make_png(path.parent / "sample.png")
    page.insert_image(pymupdf.Rect(72, 72, 172, 152), filename=image_path)
    page.insert_text((72, 180), "A figure caption body text.", fontsize=11)

    document.new_page()  # intentionally blank → scanned page

    document.set_toc([[1, "Introduction", 1], [1, "Getting Started", 2]])
    document.save(str(path))
    document.close()
    return str(path)


def flowchart_png() -> bytes:
    """Synthetic colored flowchart resembling an embedded book figure."""
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 45, 220, 130), outline="#172344", width=4, fill="#dbeafe")
    draw.rectangle((380, 265, 550, 350), outline="#172344", width=4, fill="#dbeafe")
    draw.line([(220, 87), (300, 87), (300, 307), (380, 307)], fill="#172344", width=4)
    draw.polygon([(380, 307), (367, 297), (367, 317)], fill="#172344")
    draw.text((82, 77), "Start", fill="#142033")
    draw.text((414, 296), "Finish", fill="#142033")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
