"""Stub conversion stage (Phase 1 placeholder).

Phase 2 replaces this with the real extraction stages (chapter detection,
text/table/image extraction, Mermaid conversion, Markdown assembly). It exists
now so the complete plumbing — queue → worker → stages → progress → SSE — is
exercisable end-to-end before the heavy lifting lands.
"""

import asyncio
from typing import Any

from app.domain.results import PipelineContext
from app.services.pipeline.stages.base import BasePipelineStage

_STEPS = 5


class StubConversionStage(BasePipelineStage):
    """Emits realistic progress without doing real conversion work yet."""

    name = "convert"

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        for step in range(1, _STEPS + 1):
            await asyncio.sleep(1)
            percent = 10 + int(step / _STEPS * 85)
            await self.report(ctx, percent, f"Conversion in progress (stub step {step}/{_STEPS})…")
        ctx.data["chapter_count"] = 0
        await self.report(ctx, 95, "Stub conversion finished — real extraction lands in Phase 2")
        return {"stub": True}
