"""Tests for the PDF primitives wrapper."""

from pathlib import Path

import pymupdf
import pytest

from app.domain.exceptions import ValidationError
from app.services.pdf_utils import count_pdf_pages


def _write_pdf(path: Path, pages: int) -> None:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"Page {index + 1}")
    document.save(str(path))
    document.close()


def test_count_pdf_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    _write_pdf(pdf, pages=3)
    assert count_pdf_pages(str(pdf)) == 3


def test_count_pdf_pages_rejects_garbage(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"this is definitely not a pdf")
    with pytest.raises(ValidationError):
        count_pdf_pages(str(garbage))
