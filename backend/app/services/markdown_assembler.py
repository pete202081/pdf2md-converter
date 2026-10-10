"""Markdown assembly (Builder pattern).

Takes one chapter's content blocks, orders them by ``(page, y)`` and renders the
final Markdown document: YAML frontmatter, headings, paragraphs, lists, merged
code fences, tables, image embeds and page breaks. Pure and synchronous —
trivially unit-testable, no IO.
"""

import json

from app.domain.chapters import ChapterInfo
from app.services.pipeline.blocks import (
    CODE,
    HEADING,
    IMAGE,
    LIST_ITEM,
    MERMAID,
    PAGE_BREAK,
    ContentBlock,
)


class MarkdownAssembler:
    """Builds the Markdown document for a single chapter."""

    def build(self, chapter: ChapterInfo, blocks: list[ContentBlock]) -> str:
        """Render ``blocks`` into the chapter's Markdown document."""
        ordered = sorted(blocks, key=lambda block: (block.page, block.y))
        while ordered and ordered[-1].kind == PAGE_BREAK:
            ordered.pop()  # no dangling page break at the end of the chapter

        parts: list[str] = [self._frontmatter(chapter)]
        code_lines: list[str] = []
        for block in ordered:
            if block.kind == CODE:
                code_lines.append(block.text)  # merge consecutive code lines
                continue
            if code_lines:
                parts.append("```\n" + "\n".join(code_lines) + "\n```")
                code_lines = []
            parts.append(self._render_block(block))
        if code_lines:
            parts.append("```\n" + "\n".join(code_lines) + "\n```")

        return "\n\n".join(parts) + "\n"

    @staticmethod
    def _frontmatter(chapter: ChapterInfo) -> str:
        # json.dumps produces a valid YAML double-quoted scalar (escapes included)
        return (
            "---\n"
            f"title: {json.dumps(chapter.title)}\n"
            f"order: {chapter.order_index}\n"
            f"source_pages: {chapter.start_page + 1}-{chapter.end_page + 1}\n"
            "---"
        )

    @staticmethod
    def _render_block(block: ContentBlock) -> str:
        if block.kind == HEADING:
            level = min(max(block.level, 1), 6)
            return f"{'#' * level} {block.text}"
        if block.kind == LIST_ITEM:
            return f"- {block.text}"
        if block.kind == IMAGE:
            # Chapter files live in "<folder>/chapter.md"; assets in "assets/" —
            # one level up from the chapter file.
            image = f"![{block.alt}](../{block.image_path})"
            if block.review_id:
                return (
                    f"<!-- pdf2md-review:{block.review_id}:start -->\n"
                    f"{image}\n<!-- pdf2md-review:{block.review_id}:end -->"
                )
            return image
        if block.kind == MERMAID:
            # Keep a clickable reference to the original image for comparison.
            original = (
                f"\n\n[Original diagram image](../{block.image_path})" if block.image_path else ""
            )
            return f"```mermaid\n{block.text}\n```{original}"
        if block.kind == PAGE_BREAK:
            return "<!-- page break -->\n\n---"
        # PARAGRAPH, TABLE and anything else render as their text
        return block.text
