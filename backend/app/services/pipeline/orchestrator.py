"""Conversion orchestrator (Saga-style sequencing of pipeline stages).

Responsibilities:
- transition the job QUEUED → RUNNING → SUCCEEDED/FAILED/PARTIAL
- create and update the per-stage audit records
- build the shared :class:`PipelineContext` for the job
- strict-abort on failed stages; soft diagram fallbacks yield PARTIAL
- sync whitelisted context values (page_count, chapter_count, output_path) back onto the job
"""

from typing import Any

from redis.asyncio import Redis

from app.adapters.storage.file_store import IFileStore
from app.config import Settings
from app.db.models import utcnow
from app.domain.enums import JobStatus, StageStatus
from app.domain.exceptions import NotFoundError
from app.domain.results import PipelineContext, StageResult
from app.logging import get_logger
from app.repositories.base import IJobRepository, IStageRepository
from app.services.pipeline.stages.base import BasePipelineStage

# Context keys that are mirrored onto the job row after each stage.
_SYNCED_JOB_FIELDS = ("page_count", "chapter_count", "output_path")


class ConversionOrchestrator:
    """Runs the ordered list of stages for one job."""

    def __init__(
        self,
        stages: list[BasePipelineStage],
        job_repository: IJobRepository,
        stage_repository: IStageRepository,
        file_store: IFileStore,
        settings: Settings,
        metrics_redis: Redis | None = None,
        upload_path_override: str | None = None,
    ) -> None:
        self._stages = list(stages)
        self._job_repository = job_repository
        self._stage_repository = stage_repository
        self._file_store = file_store
        self._settings = settings
        self._metrics_redis = metrics_redis
        self._upload_path_override = upload_path_override
        self._logger = get_logger("ConversionOrchestrator")

    async def run(self, job_id: str) -> JobStatus:
        """Execute all stages for ``job_id`` and return the final job status."""
        job = await self._job_repository.get(job_id)
        if job is None:
            raise NotFoundError(f"Job '{job_id}' not found")

        await self._job_repository.update_status(job_id, JobStatus.RUNNING, started_at=utcnow())

        ctx = PipelineContext(
            job_id=job_id,
            upload_path=self._upload_path_override or job.stored_path,
            output_dir=self._file_store.get_output_root(job_id),
            settings=self._settings,
        )

        failed_stage: str | None = None
        for stage in self._stages:
            stage_row = await self._stage_repository.create(
                job_id=job_id, stage_name=stage.name, status=StageStatus.PENDING
            )
            await self._stage_repository.mark(
                stage_row.id, StageStatus.RUNNING, started_at=utcnow()
            )

            result = await stage.run(ctx)
            await self._record_stage_metric(stage.name, result)

            await self._stage_repository.mark(
                stage_row.id,
                result.status,
                finished_at=result.finished_at,
                error=result.error,
                metrics=result.metrics,
            )
            await self._sync_context(job_id, ctx)

            if result.status == StageStatus.FAILED:
                failed_stage = stage.name
                self._logger.error(
                    "pipeline_aborted",
                    job_id=job_id,
                    stage=stage.name,
                    error=result.error,
                )
                break  # strict abort for hard stage failures.

        final_status = (
            JobStatus.FAILED
            if failed_stage
            else JobStatus.PARTIAL
            if ctx.data.get("partial")
            else JobStatus.SUCCEEDED
        )
        await self._job_repository.update_status(
            job_id,
            final_status,
            finished_at=utcnow(),
            error=(
                f"Stage '{failed_stage}' failed"
                if failed_stage
                else f"{ctx.data.get('diagram_fallback_count', 0)} diagram(s) kept as images"
                if final_status == JobStatus.PARTIAL
                else None
            ),
        )
        self._logger.info("pipeline_finished", job_id=job_id, status=final_status.value)
        return final_status

    async def _record_stage_metric(self, name: str, result: StageResult) -> None:
        if self._metrics_redis is None:
            return
        try:
            elapsed = max(0, int((result.finished_at - result.started_at).total_seconds() * 1000))
            await self._metrics_redis.hincrby("pdf2md:metrics:stage_ms", name, elapsed)  # type: ignore[misc]
            await self._metrics_redis.hincrby("pdf2md:metrics:stage_count", name, 1)  # type: ignore[misc]
            if name == "convert_mermaid":
                if result.metrics.get("fallback", 0):
                    await self._metrics_redis.incrby(
                        "pdf2md:metrics:fallbacks", int(result.metrics["fallback"])
                    )
                if result.status == StageStatus.FAILED or result.metrics.get("fallback", 0):
                    await self._metrics_redis.incr("pdf2md:metrics:provider_errors")
        except Exception:
            # Metrics must not fail a conversion or publish incomplete job state.
            self._logger.warning("metrics_recording_failed", stage=name)

    async def _sync_context(self, job_id: str, ctx: PipelineContext) -> None:
        """Mirror whitelisted stage outputs onto the job row."""
        updates: dict[str, Any] = {
            key: ctx.data[key] for key in _SYNCED_JOB_FIELDS if key in ctx.data
        }
        if updates:
            job = await self._job_repository.get(job_id)
            if job is not None:
                # Do not expose ephemeral worker paths before object publication.
                if job.storage_backend == "minio" and not str(
                    updates.get("output_path", "")
                ).startswith("s3://"):
                    updates.pop("output_path", None)
                if updates:
                    await self._job_repository.update_status(job_id, job.status, **updates)
