"""Stage: vision-provider diagram transcription with per-image fallback.

On any provider/validation error the original IMAGE block is left intact and
``ctx.data['partial']`` is set; assembly and packaging still run. A successful
conversion replaces the positioned image block with a MERMAID block but keeps
the original asset linked for comparison and packaged in the ZIP.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any

from app.adapters.storage.file_store import IFileStore
from app.adapters.vlm.base import IVlmProvider
from app.domain.exceptions import ProviderError
from app.domain.results import PipelineContext
from app.services.cancellation import JobCancellation
from app.services.diagram_classifier import prepare_vision_image
from app.services.mermaid_validation import validate_mermaid
from app.services.pipeline.blocks import MERMAID, ContentBlock
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService


class ConvertMermaidStage(BasePipelineStage):
    """Replaces diagram candidates with validated Mermaid when available."""

    name = "convert_mermaid"

    def __init__(
        self, progress: ProgressService, file_store: IFileStore, provider: IVlmProvider
    ) -> None:
        super().__init__(progress)
        self._store = file_store
        self._provider = provider

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        blocks: list[ContentBlock] = ctx.data.get("blocks") or []
        candidates: list[int] = ctx.data.get("diagram_candidates") or []
        conversions: dict[str, str] = {}
        failures: dict[str, str] = {}
        converted = 0
        fallback = 0
        provider_disabled = False
        review_items: list[dict[str, str | int | float]] = []

        def keep_original(block: ContentBlock, reason: str) -> None:
            nonlocal fallback
            fallback += 1
            block.review_id = uuid.uuid4().hex
            review_items.append(
                {
                    "id": block.review_id,
                    "page_number": block.page + 1,
                    "occurrence_index": number,
                    "position_y": block.y,
                    "asset_name": Path(block.image_path).name,
                    "reason": reason,
                }
            )

        for number, index in enumerate(candidates, start=1):
            await ctx.ensure_active()
            block = blocks[index]
            try:
                if provider_disabled:
                    keep_original(block, "vision_unavailable")
                    continue
                if block.image_path in failures:
                    keep_original(block, failures[block.image_path])
                    continue
                if block.image_path not in conversions:
                    path = self._store.get_output_path(ctx.job_id, block.image_path)
                    image_bytes = await asyncio.to_thread(Path(path).read_bytes)
                    png = await asyncio.to_thread(prepare_vision_image, image_bytes)
                    response = await self._provider.diagram_to_mermaid(png, "image/png")
                    conversions[block.image_path] = validate_mermaid(response)
                code = conversions[block.image_path]
                blocks[index] = ContentBlock(
                    kind=MERMAID,
                    page=block.page,
                    y=block.y,
                    text=code,
                    image_path=block.image_path,
                    alt=block.alt,
                    bbox=block.bbox,
                )
                converted += 1
            except JobCancellation:
                raise
            except Exception as exc:
                # Preserve the original IMAGE block and record a safe reason category.
                reason = (
                    "vision_or_validation_failed"
                    if isinstance(exc, ProviderError)
                    else "asset_error"
                )
                if isinstance(exc, ProviderError):
                    provider_disabled = True
                    self._logger.warning(
                        "diagram_vision_disabled",
                        job_id=ctx.job_id,
                        page=block.page,
                        error=str(exc),
                        remaining=len(candidates) - number,
                    )
                else:
                    self._logger.warning(
                        "diagram_asset_conversion_failed",
                        job_id=ctx.job_id,
                        page=block.page,
                        error=str(exc),
                    )
                failures[block.image_path] = reason
                keep_original(block, reason)
            if number == len(candidates) or number % 5 == 0:
                await self.report(
                    ctx,
                    76 + int(6 * number / len(candidates)),
                    f"Converting diagrams… {number}/{len(candidates)}",
                )
        ctx.data["blocks"] = blocks
        ctx.data["diagram_fallback_count"] = fallback
        ctx.data["diagram_reviews"] = review_items
        if fallback:
            ctx.data["partial"] = True
        await self.report(
            ctx, 82, f"Converted {converted} diagram(s); retained {fallback} original(s)"
        )
        return {
            "candidates": len(candidates),
            "converted": converted,
            "fallback": fallback,
            "vision_disabled": provider_disabled,
        }
