"""Tests for table extraction and Markdown table rendering."""

from pathlib import Path

import pdfplumber
import pymupdf

from app.services.pipeline.blocks import TABLE
from app.services.table_extractor import TableExtractor, render_markdown_table
from tests.helpers import draw_table


def test_render_markdown_table() -> None:
    markdown = render_markdown_table([["Name", "Age"], ["Alice", "30"], ["Bob", "25"]])
    assert markdown == ("| Name | Age |\n| --- | --- |\n| Alice | 30 |\n| Bob | 25 |")


def test_render_markdown_table_handles_none_and_pipes() -> None:
    markdown = render_markdown_table([["A|B", None], ["x", "y"]])
    assert markdown is not None
    assert "A\\|B" in markdown
    assert "|  |" in markdown  # None → empty cell


def test_render_markdown_table_rejects_degenerate_tables() -> None:
    assert render_markdown_table([]) is None
    assert render_markdown_table([["only", "header"]]) is None  # no data rows
    assert render_markdown_table([[None, None]]) is None


def test_extract_table_from_pdf_page(tmp_path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    draw_table(page, [["Name", "Age", "City"], ["Alice", "30", "Paris"], ["Bob", "25", "Lyon"]])
    path = tmp_path / "table.pdf"
    document.save(str(path))
    document.close()

    extractor = TableExtractor()
    with pdfplumber.open(str(path)) as pdf:
        blocks = extractor.extract_page(pdf.pages[0], 0)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.kind == TABLE
    assert block.page == 0
    assert "| Name | Age | City |" in block.text
    assert "| --- | --- | --- |" in block.text
    assert "| Alice | 30 | Paris |" in block.text
    assert "| Bob | 25 | Lyon |" in block.text


def test_extract_from_page_without_tables(tmp_path: Path) -> None:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Just text, no table.", fontsize=11)
    path = tmp_path / "no_table.pdf"
    document.save(str(path))
    document.close()

    extractor = TableExtractor()
    with pdfplumber.open(str(path)) as pdf:
        assert extractor.extract_page(pdf.pages[0], 0) == []
