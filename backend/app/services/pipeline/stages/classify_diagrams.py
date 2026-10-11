"""Stage: classify embedded images as likely diagrams or ordinary photos/scans."""

import asyncio
from pathlib import Path
from typing import Any

from app.adapters.storage.file_store import IFileStore
from app.domain.results import PipelineContext
from app.services.diagram_classifier import DiagramClassifier
from app.services.pipeline.blocks import IMAGE, ContentBlock
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService


class ClassifyDiagramsStage(BasePipelineStage):
    """Select candidate IMAGE block indices without altering the block list."""

    name = "classify_diagrams"

    def __init__(
        self,
        progress: ProgressService,
        file_store: IFileStore,
        classifier: DiagramClassifier | None = None,
    ) -> None:
        super().__init__(progress)
        self._store = file_store
        self._classifier = classifier or DiagramClassifier()

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        blocks: list[ContentBlock] = ctx.data.get("blocks") or []
        candidates: list[int] = []
        cache: dict[str, bytes] = {}
        for index, block in enumerate(blocks):
            await ctx.ensure_active()
            if block.kind != IMAGE or not block.image_path:
                continue
            try:
                if block.image_path not in cache:
                    path = self._store.get_output_path(ctx.job_id, block.image_path)
                    cache[block.image_path] = await asyncio.to_thread(Path(path).read_bytes)
                if await asyncio.to_thread(
                    self._classifier.is_diagram, cache[block.image_path], block.bbox
                ):
                    candidates.append(index)
            except (OSError, ValueError):
                self._logger.exception(
                    "diagram_classification_skipped", job_id=ctx.job_id, page=block.page
                )
        ctx.data["diagram_candidates"] = candidates
        await self.report(ctx, 75, f"Found {len(candidates)} likely diagram(s)")
        return {"inspected": len(cache), "candidates": len(candidates)}
