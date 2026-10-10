"""Thin, testable wrappers around PDF primitives."""

import pymupdf

from app.domain.exceptions import ValidationError


def count_pdf_pages(path: str) -> int:
    """Return the number of pages of a PDF.

    Raises:
        ValidationError: if the file cannot be opened as a PDF (corrupt,
            encrypted, or not a PDF at all).
    """
    try:
        with pymupdf.open(path) as document:
            return document.page_count
    except Exception as exc:
        raise ValidationError(
            "Could not read the PDF file — it may be corrupt, encrypted, or not a real PDF"
        ) from exc
