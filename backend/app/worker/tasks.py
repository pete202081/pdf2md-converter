"""Celery tasks."""

from __future__ import annotations

import asyncio
import tempfile

from celery import Task

from app.adapters.storage.file_store import LocalFileStore
from app.config import get_settings
from app.container import CONVERSION_TASK_NAME, build_container
from app.db.models import utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import AppError, ProcessingError
from app.logging import configure_logging, get_logger
from app.services.runtime_settings import RuntimeSettingsService
from app.worker.celery_app import celery_app

logger = get_logger("worker")


@celery_app.task(bind=True, name=CONVERSION_TASK_NAME)
def run_conversion(self: Task, job_id: str) -> str:
    """Celery entrypoint: run the conversion pipeline for one job."""
    return asyncio.run(_run_conversion(job_id))


async def _run_conversion(job_id: str) -> str:
    """Async body of the conversion task.

    Builds its own container (the worker has no FastAPI DI), runs the
    orchestrator, guarantees a terminal progress event, and always closes
    resources. Even a catastrophic failure leaves the job in a terminal state
    with a recorded error — the job can never hang in RUNNING.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)
    staging: tempfile.TemporaryDirectory[str] | None = None
    try:
        try:
            await container.redis.incr("pdf2md:metrics:active")
            await container.redis.expire("pdf2md:metrics:active", 1200)
        except Exception:
            logger.warning("metrics_worker_start_failed")
        async with container.database.session() as session:
            job_repository = container.job_repository(session)
            try:
                job = await job_repository.get(job_id)
                if job is None:
                    raise ProcessingError("Job not found")
                container.settings = await RuntimeSettingsService(session, settings).resolve()
                local_upload: str | None = None
                if job.storage_backend == "minio":
                    if not container.object_store:
                        raise ProcessingError("MinIO credentials are not configured")
                    staging = tempfile.TemporaryDirectory(prefix="pdf2md-worker-")
                    container.file_store = LocalFileStore(
                        staging.name + "/uploads", staging.name + "/outputs"
                    )
                    local_upload = container.file_store.get_upload_path(job_id, job.filename)
                    await container.object_store.download(job.stored_path, local_upload)
                orchestrator = container.build_orchestrator(
                    session, upload_path_override=local_upload
                )
                status = await orchestrator.run(job_id)
            except AppError as exc:
                logger.error("conversion_failed", job_id=job_id, error=exc.message)
                await job_repository.update_status(
                    job_id, JobStatus.FAILED, error=exc.message, finished_at=utcnow()
                )
                status = JobStatus.FAILED
            except Exception:
                logger.exception("conversion_crashed", job_id=job_id)
                await job_repository.update_status(
                    job_id,
                    JobStatus.FAILED,
                    error="Unexpected worker error",
                    finished_at=utcnow(),
                )
                status = JobStatus.FAILED

            await container.progress_service(session).report(
                job_id,
                "orchestrator",
                100,
                f"Job finished with status {status.value}",
                terminal=True,
            )
            return status.value
    finally:
        try:
            await container.redis.decr("pdf2md:metrics:active")
        except Exception:
            logger.warning("metrics_worker_end_failed")
        if staging is not None:
            staging.cleanup()
        await container.close()
