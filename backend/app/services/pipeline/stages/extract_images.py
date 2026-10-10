"""Stage: extract embedded images to disk and append IMAGE blocks."""

from typing import Any

import pymupdf

from app.domain.chapters import ChapterInfo
from app.domain.results import PipelineContext
from app.services.image_extractor import ImageExtractor
from app.services.pipeline.blocks import ContentBlock
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService


class ExtractImagesStage(BasePipelineStage):
    """Appends IMAGE blocks to ``ctx.data['blocks']`` and saves image files."""

    name = "extract_images"

    def __init__(self, progress: ProgressService, image_extractor: ImageExtractor) -> None:
        super().__init__(progress)
        self._extractor = image_extractor

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        await self.report(ctx, 59, "Extracting images…")
        chapters: list[ChapterInfo] = ctx.data.get("chapters") or []
        blocks: list[ContentBlock] = list(ctx.data.get("blocks") or [])

        found = 0
        with pymupdf.open(ctx.upload_path) as document:
            for chapter in chapters:
                for page_number in range(chapter.start_page, chapter.end_page + 1):
                    page_blocks = await self._extractor.extract_page(
                        document, ctx.job_id, page_number
                    )
                    blocks.extend(page_blocks)
                    found += len(page_blocks)

        ctx.data["blocks"] = blocks
        await self.report(ctx, 72, f"Extracted {found} image(s)")
        return {"images": found}
