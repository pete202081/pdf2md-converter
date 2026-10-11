"""Base class for pipeline stages (Template Method pattern).

``run()`` is the fixed skeleton: timing, structured logging and uniform error
capture. Subclasses only implement ``execute()`` — they cannot forget to
handle errors, because the template guarantees it (Open/Closed: new stages are
added by subclassing, existing code is untouched).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.db.models import utcnow
from app.domain.enums import StageStatus
from app.domain.exceptions import AppError
from app.domain.results import PipelineContext, StageResult
from app.logging import get_logger
from app.services.cancellation import JobCancellation
from app.services.progress_service import ProgressService


class BasePipelineStage(ABC):
    """Template-method base for all conversion stages."""

    name: ClassVar[str] = "base_stage"

    def __init__(self, progress: ProgressService) -> None:
        self._progress = progress
        self._logger = get_logger(f"stage.{self.name}")

    async def run(self, ctx: PipelineContext) -> StageResult:
        """Execute the stage with timing, logging and error capture."""
        started_at = utcnow()
        self._logger.info("stage_started", job_id=ctx.job_id, stage=self.name)
        try:
            metrics = await self.execute(ctx)
        except JobCancellation:
            raise
        except AppError as exc:
            self._logger.error(
                "stage_failed", job_id=ctx.job_id, stage=self.name, error=exc.message
            )
            return StageResult(
                stage=self.name,
                status=StageStatus.FAILED,
                started_at=started_at,
                finished_at=utcnow(),
                error=exc.message,
            )
        except Exception:
            self._logger.exception("stage_crashed", job_id=ctx.job_id, stage=self.name)
            return StageResult(
                stage=self.name,
                status=StageStatus.FAILED,
                started_at=started_at,
                finished_at=utcnow(),
                error="Unexpected error in stage",
            )
        duration_ms = int((utcnow() - started_at).total_seconds() * 1000)
        self._logger.info(
            "stage_finished", job_id=ctx.job_id, stage=self.name, duration_ms=duration_ms
        )
        return StageResult(
            stage=self.name,
            status=StageStatus.SUCCEEDED,
            started_at=started_at,
            finished_at=utcnow(),
            metrics=metrics,
        )

    @abstractmethod
    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        """Do the stage's work. Return a metrics dict.

        Raise an :class:`AppError` subclass for expected, reportable failures.
        """

    async def report(self, ctx: PipelineContext, percent: int, message: str) -> None:
        """Convenience wrapper for progress reporting."""
        await ctx.ensure_active()
        await self._progress.report(ctx.job_id, self.name, percent, message)
