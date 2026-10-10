"""Tests for the Markdown assembler (Builder)."""

from app.domain.chapters import ChapterInfo
from app.services.markdown_assembler import MarkdownAssembler
from app.services.pipeline.blocks import (
    CODE,
    HEADING,
    IMAGE,
    LIST_ITEM,
    PAGE_BREAK,
    PARAGRAPH,
    TABLE,
    ContentBlock,
)


def _chapter() -> ChapterInfo:
    return ChapterInfo(
        order_index=2,
        title="Getting Started",
        folder_name="02_Getting_Started",
        start_page=1,
        end_page=2,
    )


def test_build_renders_frontmatter_and_blocks_in_position_order() -> None:
    chapter = _chapter()
    blocks = [
        ContentBlock(kind=PARAGRAPH, page=1, y=200, text="Second on page 1"),
        ContentBlock(kind=HEADING, page=1, y=50, text="Getting Started", level=1),
        ContentBlock(kind=LIST_ITEM, page=1, y=120, text="install it"),
        ContentBlock(
            kind=IMAGE, page=2, y=80, image_path="assets/img_p3_x7.png", alt="img_p3_x7.png"
        ),
        ContentBlock(kind=TABLE, page=2, y=300, text="| A |\n| --- |\n| 1 |"),
        ContentBlock(kind=PAGE_BREAK, page=1, y=float("inf")),
        ContentBlock(kind=PAGE_BREAK, page=2, y=float("inf")),
    ]

    markdown = MarkdownAssembler().build(chapter, blocks)

    assert markdown.startswith("---\n")
    assert 'title: "Getting Started"' in markdown
    assert "order: 2" in markdown
    assert "source_pages: 2-3" in markdown

    # Blocks are ordered by (page, y), not by input order
    heading_pos = markdown.index("# Getting Started")
    list_pos = markdown.index("- install it")
    para_pos = markdown.index("Second on page 1")
    image_pos = markdown.index("![img_p3_x7.png](../assets/img_p3_x7.png)")
    table_pos = markdown.index("| A |")
    assert heading_pos < list_pos < para_pos < image_pos < table_pos

    # The trailing page break is dropped
    assert markdown.rstrip().endswith("| 1 |")


def test_build_merges_consecutive_code_blocks_into_one_fence() -> None:
    blocks = [
        ContentBlock(kind=CODE, page=0, y=10, text="line one"),
        ContentBlock(kind=CODE, page=0, y=30, text="line two"),
        ContentBlock(kind=PARAGRAPH, page=0, y=60, text="after code"),
        ContentBlock(kind=CODE, page=0, y=90, text="line three"),
    ]

    markdown = MarkdownAssembler().build(_chapter(), blocks)

    assert "```\nline one\nline two\n```" in markdown
    assert "```\nline three\n```" in markdown
    assert markdown.index("after code") > markdown.index("line two")


def test_build_renders_page_breaks_between_pages() -> None:
    blocks = [
        ContentBlock(kind=PARAGRAPH, page=0, y=10, text="page one"),
        ContentBlock(kind=PAGE_BREAK, page=0, y=float("inf")),
        ContentBlock(kind=PARAGRAPH, page=1, y=10, text="page two"),
    ]

    markdown = MarkdownAssembler().build(_chapter(), blocks)

    assert "page one" in markdown
    assert "<!-- page break -->" in markdown
    assert "page two" in markdown
    assert markdown.index("page one") < markdown.index("page two")


def test_build_clamps_heading_levels() -> None:
    blocks = [ContentBlock(kind=HEADING, page=0, y=10, text="Deep", level=9)]
    markdown = MarkdownAssembler().build(_chapter(), blocks)
    assert "###### Deep" in markdown
