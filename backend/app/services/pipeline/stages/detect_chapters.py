"""Stage: detect chapter boundaries (outline → heading heuristics → fallback)."""

import asyncio
from typing import Any

import pymupdf

from app.domain.chapters import ChapterInfo
from app.domain.results import PipelineContext
from app.services.chapter_detector import ChapterDetector
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService


class DetectChaptersStage(BasePipelineStage):
    """Fills ``ctx.data['chapters']`` with the ordered :class:`ChapterInfo` list."""

    name = "detect_chapters"

    def __init__(self, progress: ProgressService, detector: ChapterDetector | None = None) -> None:
        super().__init__(progress)
        self._detector = detector or ChapterDetector()

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        await self.report(ctx, 10, "Detecting chapters…")

        def _detect() -> list[ChapterInfo]:
            with pymupdf.open(ctx.upload_path) as document:
                return self._detector.detect(document)

        chapters = await asyncio.to_thread(_detect)
        ctx.data["chapters"] = chapters
        ctx.data["chapter_count"] = len(chapters)
        await self.report(ctx, 14, f"Detected {len(chapters)} chapter(s)")
        return {"chapter_count": len(chapters)}
