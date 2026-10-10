"""Stage: extract tables as Markdown blocks (pdfplumber)."""

import asyncio
from typing import Any

import pdfplumber

from app.domain.chapters import ChapterInfo
from app.domain.results import PipelineContext
from app.services.pipeline.blocks import CODE, HEADING, LIST_ITEM, PARAGRAPH, TABLE, ContentBlock
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService
from app.services.table_extractor import TableExtractor


class ExtractTablesStage(BasePipelineStage):
    """Appends TABLE blocks to ``ctx.data['blocks']``."""

    name = "extract_tables"

    def __init__(
        self, progress: ProgressService, table_extractor: TableExtractor | None = None
    ) -> None:
        super().__init__(progress)
        self._extractor = table_extractor or TableExtractor()

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        await self.report(ctx, 46, "Extracting tables…")
        chapters: list[ChapterInfo] = ctx.data.get("chapters") or []
        blocks: list[ContentBlock] = list(ctx.data.get("blocks") or [])

        def _extract_all() -> list[ContentBlock]:
            found: list[ContentBlock] = []
            with pdfplumber.open(ctx.upload_path) as pdf:
                for chapter in chapters:
                    for page_number in range(chapter.start_page, chapter.end_page + 1):
                        found.extend(
                            self._extractor.extract_page(pdf.pages[page_number], page_number)
                        )
            return found

        found = await asyncio.to_thread(_extract_all)
        tables_by_page: dict[int, list[ContentBlock]] = {}
        for table in found:
            tables_by_page.setdefault(table.page, []).append(table)
            rows = [row.strip("| ").split("|") for row in table.text.splitlines()[2:]]
            cells = [cell.strip() for row in rows for cell in row]
            if len(cells) >= 4 and sum(not cell for cell in cells) * 2 >= len(cells):
                hints = ctx.data.setdefault("quality_hints", {})
                hints.setdefault(table.page + 1, {})["SPARSE_TABLE"] = (
                    "Extracted table has many empty cells; compare with the source"
                )
        # Remove text blocks physically inside detected tables; otherwise each
        # cell would appear twice (once as flattened PDF text, once in Markdown).
        text_kinds = {PARAGRAPH, HEADING, LIST_ITEM, CODE}
        blocks = [
            block
            for block in blocks
            if block.kind not in text_kinds
            or block.bbox is None
            or not any(
                table.kind == TABLE
                and table.bbox is not None
                and table.bbox[0] <= (block.bbox[0] + block.bbox[2]) / 2 <= table.bbox[2]
                and table.bbox[1] <= (block.bbox[1] + block.bbox[3]) / 2 <= table.bbox[3]
                for table in tables_by_page.get(block.page, [])
            )
        ]
        blocks.extend(found)
        ctx.data["blocks"] = blocks
        await self.report(ctx, 58, f"Extracted {len(found)} table(s)")
        return {"tables": len(found)}
