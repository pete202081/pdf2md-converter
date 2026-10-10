"""Tests for text extraction: headings, paragraphs, lists, code, OCR fallback."""

from pathlib import Path

import pymupdf
import pytest

from app.domain.exceptions import ProcessingError
from app.services.pdf_stats import estimate_body_size
from app.services.pipeline.blocks import (
    CODE,
    HEADING,
    LIST_ITEM,
    PAGE_BREAK,
    PARAGRAPH,
)
from app.services.text_extractor import TextExtractor
from tests.fakes import FailingOcrEngine, FakeOcrEngine


def _build_pdf(tmp_path: Path) -> str:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Chapter 1 Intro", fontsize=20)
    page.insert_text((72, 100), "This is a body paragraph.", fontsize=11)
    page.insert_text((72, 120), "- first item", fontsize=11)
    page.insert_text((72, 140), "- second item", fontsize=11)
    page = document.new_page()
    page.insert_text((72, 72), "print('hello')", fontname="cour", fontsize=10)
    page.insert_text((72, 90), "print('world')", fontname="cour", fontsize=10)
    document.new_page()  # blank → scanned page
    path = tmp_path / "text.pdf"
    document.save(str(path))
    document.close()
    return str(path)


def test_extracts_headings_paragraphs_and_lists(tmp_path: Path) -> None:
    path = _build_pdf(tmp_path)
    extractor = TextExtractor(FakeOcrEngine())

    with pymupdf.open(path) as document:
        body_size = estimate_body_size(document)
        blocks = extractor.extract_page(document, 0, body_size)

    kinds = [block.kind for block in blocks]
    assert kinds == [HEADING, PARAGRAPH, LIST_ITEM, LIST_ITEM, PAGE_BREAK]

    heading = blocks[0]
    assert heading.text == "Chapter 1 Intro"
    assert heading.level == 1
    assert blocks[1].text == "This is a body paragraph."
    assert [block.text for block in blocks[2:4]] == ["first item", "second item"]


def test_extracts_monospace_lines_as_code(tmp_path: Path) -> None:
    path = _build_pdf(tmp_path)
    extractor = TextExtractor(FakeOcrEngine())

    with pymupdf.open(path) as document:
        body_size = estimate_body_size(document)
        blocks = extractor.extract_page(document, 1, body_size)

    code_blocks = [block for block in blocks if block.kind == CODE]
    assert [block.text for block in code_blocks] == ["print('hello')", "print('world')"]
    # The Markdown assembler merges adjacent CODE blocks into a single fence.


def test_blank_page_uses_ocr_fallback(tmp_path: Path) -> None:
    path = _build_pdf(tmp_path)
    ocr = FakeOcrEngine(text="Scanned page text\n\nSecond paragraph")
    extractor = TextExtractor(ocr)

    with pymupdf.open(path) as document:
        blocks = extractor.extract_page(document, 2, body_size=11.0)

    assert ocr.calls == 1
    paragraphs = [block for block in blocks if block.kind == PARAGRAPH]
    assert [block.text for block in paragraphs] == [
        "Scanned page text",
        "Second paragraph",
    ]
    assert blocks[-1].kind == PAGE_BREAK


def test_ocr_failure_fails_instead_of_silently_losing_page(tmp_path: Path) -> None:
    path = _build_pdf(tmp_path)
    extractor = TextExtractor(FailingOcrEngine())

    with pymupdf.open(path) as document, pytest.raises(ProcessingError, match="OCR failed"):
        extractor.extract_page(document, 2, body_size=11.0)
