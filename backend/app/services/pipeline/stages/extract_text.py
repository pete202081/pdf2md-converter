"""Stage: extract text blocks page by page (with OCR fallback for scanned pages)."""

import asyncio
from typing import Any

import pymupdf

from app.domain.chapters import ChapterInfo
from app.domain.results import PipelineContext
from app.services.pdf_stats import estimate_body_size
from app.services.pipeline.blocks import ContentBlock
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService
from app.services.text_extractor import TextExtractor


class ExtractTextStage(BasePipelineStage):
    """Fills ``ctx.data['blocks']`` with text blocks for every chapter page."""

    name = "extract_text"

    def __init__(self, progress: ProgressService, text_extractor: TextExtractor) -> None:
        super().__init__(progress)
        self._extractor = text_extractor

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        chapters: list[ChapterInfo] = ctx.data.get("chapters") or []
        total_pages = sum(chapter.page_count for chapter in chapters) or 1
        blocks: list[ContentBlock] = []
        done = 0

        with pymupdf.open(ctx.upload_path) as document:
            body_size = await asyncio.to_thread(estimate_body_size, document)
            for chapter in chapters:
                for page_number in range(chapter.start_page, chapter.end_page + 1):
                    await ctx.ensure_active()
                    page_blocks = await asyncio.to_thread(
                        self._extractor.extract_page, document, page_number, body_size
                    )
                    blocks.extend(page_blocks)
                    hints = ctx.data.setdefault("quality_hints", {})
                    page_hints = hints.setdefault(page_number + 1, {})
                    if not document[page_number].get_text().strip():
                        page_hints["OCR_USED"] = "Scanned page: OCR fallback was used"
                    if sum(len(block.text.split()) for block in page_blocks) < 10:
                        page_hints["LOW_TEXT"] = "Very little text was extracted; check this page"
                    done += 1
                    if done % 10 == 0 or done == total_pages:
                        percent = 15 + int(done / total_pages * 30)
                        await self.report(
                            ctx, percent, f"Extracting text… page {done}/{total_pages}"
                        )

        ctx.data["blocks"] = blocks
        await self.report(ctx, 45, f"Extracted text from {done} page(s)")
        return {"pages": done, "blocks": len(blocks)}
