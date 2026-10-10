"""Tests for chapter detection (TOC, heading heuristics, fallback) + slugify."""

from pathlib import Path

import pymupdf

from app.domain.chapters import slugify
from app.services.chapter_detector import ChapterDetector


def test_slugify() -> None:
    assert slugify("Getting Started!") == "Getting_Started"
    assert slugify("  Advanced   Topics  ") == "Advanced_Topics"
    assert slugify("C++ & Friends") == "C_Friends"
    assert slugify("!!!") == "Chapter"
    assert slugify("") == "Chapter"


def _save(document: pymupdf.Document, path: Path) -> str:
    document.save(str(path))
    document.close()
    return str(path)


def test_detect_from_pdf_toc(tmp_path: Path) -> None:
    document = pymupdf.open()
    for index in range(4):
        page = document.new_page()
        page.insert_text((72, 72), f"Body text page {index + 1}", fontsize=11)
    document.set_toc(
        [
            [1, "Introduction", 1],
            [1, "Getting Started", 2],
            [2, "Deep Dive", 3],  # level 2 — not a chapter
            [1, "Advanced", 4],
        ]
    )
    path = _save(document, tmp_path / "toc.pdf")

    with pymupdf.open(path) as doc:
        chapters = ChapterDetector().detect(doc)

    assert [chapter.title for chapter in chapters] == [
        "Introduction",
        "Getting Started",
        "Advanced",
    ]
    assert [chapter.folder_name for chapter in chapters] == [
        "01_Introduction",
        "02_Getting_Started",
        "03_Advanced",
    ]
    assert [(c.start_page, c.end_page) for c in chapters] == [(0, 0), (1, 2), (3, 3)]
    assert [c.order_index for c in chapters] == [1, 2, 3]


def test_detect_from_heading_heuristics(tmp_path: Path) -> None:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Body text without headings.", fontsize=11)
    page = document.new_page()
    page.insert_text((72, 72), "Chapter 1 Alpha", fontsize=20)
    page.insert_text((72, 100), "Body text.", fontsize=11)
    page = document.new_page()
    page.insert_text((72, 72), "Chapter 2 Beta", fontsize=20)
    page.insert_text((72, 100), "More body text.", fontsize=11)
    path = _save(document, tmp_path / "heuristic.pdf")

    with pymupdf.open(path) as doc:
        chapters = ChapterDetector().detect(doc)

    # Content before the first heading becomes a "Front Matter" chapter
    assert [chapter.title for chapter in chapters] == [
        "Front Matter",
        "Chapter 1 Alpha",
        "Chapter 2 Beta",
    ]
    assert [(c.start_page, c.end_page) for c in chapters] == [(0, 0), (1, 1), (2, 2)]


def test_detect_fallback_single_chapter(tmp_path: Path) -> None:
    document = pymupdf.open()
    for index in range(3):
        document.new_page().insert_text((72, 72), f"Plain body {index}.", fontsize=11)
    path = _save(document, tmp_path / "plain.pdf")

    with pymupdf.open(path) as doc:
        chapters = ChapterDetector().detect(doc)

    assert len(chapters) == 1
    assert chapters[0].title == "Full Document"
    assert chapters[0].folder_name == "01_Full_Document"
    assert (chapters[0].start_page, chapters[0].end_page) == (0, 2)


def test_toc_preserves_front_matter_and_deduplicates_same_page(tmp_path: Path) -> None:
    document = pymupdf.open()
    for index in range(4):
        document.new_page().insert_text((72, 72), f"Page {index + 1}", fontsize=11)
    document.set_toc([[1, "First Chapter", 2], [1, "Also First Chapter", 2], [1, "Second", 4]])
    path = _save(document, tmp_path / "front_matter.pdf")

    with pymupdf.open(path) as doc:
        chapters = ChapterDetector().detect(doc)

    assert [c.title for c in chapters] == ["Front Matter", "First Chapter", "Second"]
    assert [(c.start_page, c.end_page) for c in chapters] == [(0, 0), (1, 2), (3, 3)]


def test_folder_prefix_expands_for_more_than_99_chapters(tmp_path: Path) -> None:
    document = pymupdf.open()
    for _ in range(100):
        document.new_page()
    document.set_toc([[1, f"Chapter {i}", i] for i in range(1, 101)])
    path = _save(document, tmp_path / "hundred.pdf")

    with pymupdf.open(path) as doc:
        chapters = ChapterDetector().detect(doc)

    assert len(chapters) == 100
    assert chapters[0].folder_name == "001_Chapter_1"
    assert chapters[-1].folder_name == "100_Chapter_100"
    assert [c.folder_name for c in chapters] == sorted(c.folder_name for c in chapters)
