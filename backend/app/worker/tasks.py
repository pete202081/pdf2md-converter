"""Celery tasks."""

from __future__ import annotations

import asyncio

from celery import Task

from app.config import get_settings
from app.container import CONVERSION_TASK_NAME, build_container
from app.db.models import utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import AppError
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
    try:
        async with container.database.session() as session:
            job_repository = container.job_repository(session)
            try:
                container.settings = await RuntimeSettingsService(session, settings).resolve()
                orchestrator = container.build_orchestrator(session)
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
        await container.close()
