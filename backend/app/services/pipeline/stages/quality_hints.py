"""Persist honest review hints without AI guesses or OCR confidence claims."""

from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, PageQualityFlag
from app.domain.chapters import ChapterInfo
from app.domain.results import PipelineContext
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService


class QualityHintsStage(BasePipelineStage):
    name = "quality_hints"

    def __init__(self, progress: ProgressService, session: AsyncSession) -> None:
        super().__init__(progress)
        self._session = session

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        chapters: list[ChapterInfo] = ctx.data.get("chapters") or []
        hints: dict[int, dict[str, str]] = ctx.data.get("quality_hints") or {}
        if len(chapters) == 1 and chapters[0].page_count > 5:
            hints.setdefault(chapters[0].start_page + 1, {})["SINGLE_CHAPTER"] = (
                "Long PDF has one detected chapter; check chapter boundaries"
            )
        await self._session.execute(
            delete(PageQualityFlag).where(PageQualityFlag.job_id == ctx.job_id)
        )
        count = 0
        for page, warnings in sorted(hints.items()):
            chapter = next((c for c in chapters if c.start_page < page <= c.end_page + 1), None)
            if chapter is None:
                continue
            for code, message in sorted(warnings.items()):
                self._session.add(
                    PageQualityFlag(
                        job_id=ctx.job_id,
                        page_number=page,
                        chapter_order=chapter.order_index,
                        code=code,
                        message=message,
                    )
                )
                count += 1
        job = await self._session.get(Job, ctx.job_id)
        if job is not None:
            job.quality_inventory_ready = True
        await self._session.commit()
        return {"warnings": count}
