"""Celery conversion entrypoint and cooperative cancellation cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from celery import Task
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage.file_store import LocalFileStore
from app.config import get_settings
from app.container import CONVERSION_TASK_NAME, Container, build_container
from app.db.models import utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import AppError, ProcessingError, StorageError
from app.logging import configure_logging, get_logger
from app.services.cancellation import JobCancellation, ensure_job_active
from app.services.pipeline.orchestrator import JobAlreadyClaimed
from app.services.progress_service import events_channel
from app.services.runtime_settings import RuntimeSettingsService
from app.worker.celery_app import celery_app

logger = get_logger("worker")


@asynccontextmanager
async def _job_lease(container: Container, job_id: str) -> AsyncIterator[bool]:
    """Hold a PostgreSQL session advisory lock until writes AND cleanup finish.

    Late Celery redelivery cannot remove a live worker's files. A crashed worker
    releases the lock, so a redelivery can finish the pending cleanup.
    """
    if container.database.engine.dialect.name != "postgresql":
        # SQLite is only used by isolated tests; Docker production uses Postgres.
        yield True
        return
    key = int.from_bytes(hashlib.sha256(job_id.encode()).digest()[:8], "big", signed=True)
    async with container.database.engine.connect() as connection:
        locked = bool(
            (
                await connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            ).scalar_one()
        )
        try:
            yield locked
        finally:
            if locked:
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


@celery_app.task(bind=True, name=CONVERSION_TASK_NAME)
def run_conversion(self: Task, job_id: str) -> str:
    return asyncio.run(_run_conversion(job_id))


async def _remove_cancelled_job(container: Container, session: AsyncSession, job_id: str) -> bool:
    """Called only by the owning worker after all stage writes have stopped."""
    await session.rollback()
    repository = container.job_repository(session)
    job = await repository.get(job_id)
    if job is None:
        return True  # Idempotent Celery redelivery after completed cleanup.
    if job.cancel_requested_at is None:
        raise ProcessingError("Job was not requested to stop")
    try:
        if job.storage_backend == "minio":
            if not container.object_store:
                raise StorageError("MinIO storage is not configured")
            remote = container.object_store
            await remote.delete_prefix(
                remote.key(job.owner_id, job.id, "upload").rsplit("/", 1)[0] + "/"
            )
        else:
            # Use the configured persistent store, not a temporary MinIO staging store.
            await LocalFileStore(
                container.settings.upload_dir, container.settings.output_dir
            ).delete_job_files(job_id)
        await repository.delete(job_id)
    except Exception:
        logger.exception("cancellation_cleanup_failed", job_id=job_id)
        await session.rollback()
        await repository.update_status(
            job_id,
            JobStatus.FAILED,
            error="Stop cleanup failed; files may remain. Use Delete job to retry cleanup.",
            finished_at=utcnow(),
        )
        await container.progress_service(session).report(
            job_id,
            "orchestrator",
            100,
            "Stop cleanup failed; delete the job to retry.",
            terminal=True,
        )
        return False
    logger.info("job_removed_after_stop", job_id=job_id)
    try:
        await container.redis.publish(
            events_channel(job_id),
            json.dumps(
                {
                    "job_id": job_id,
                    "stage": "orchestrator",
                    "percent": 100,
                    "message": "Conversion stopped; job and files removed.",
                    "ts": utcnow().isoformat(),
                    "terminal": True,
                }
            ),
        )
    except Exception:
        logger.warning("cancellation_terminal_publish_failed", job_id=job_id)
    return True


async def _run_conversion(job_id: str) -> str:
    """Claim once; stop cooperatively, then remove source/output and row."""
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
        async with _job_lease(container, job_id) as locked:
            if not locked:
                logger.info("duplicate_conversion_delivery_skipped", job_id=job_id)
                return "ALREADY_RUNNING"
            async with container.database.session() as session:
                repository = container.job_repository(session)

                async def check() -> None:
                    await ensure_job_active(session, job_id)

                try:
                    job = await repository.get(job_id)
                    if job is None:
                        return "REMOVED"  # Redelivered after cancellation completed.
                    await check()
                    if job.status == JobStatus.QUEUED:
                        if not await repository.claim(job_id):
                            await check()
                            raise JobAlreadyClaimed()
                    elif job.status != JobStatus.RUNNING:
                        # An earlier delivery already finished. Never rerun terminal jobs.
                        return job.status.value
                    else:
                        # The PostgreSQL lease proves the previous worker is gone.
                        # Restart this job from stage one after a worker crash.
                        logger.warning("resuming_orphaned_running_job", job_id=job_id)
                        key = f"pdf2md:worker_restarts:{job_id}"
                        try:
                            restarts = await container.redis.incr(key)
                            await container.redis.expire(key, 86400)
                        except Exception:
                            logger.warning("worker_restart_counter_unavailable", job_id=job_id)
                            restarts = 1
                        if restarts > 3:
                            if not await repository.finalize(
                                job_id,
                                JobStatus.FAILED,
                                error="Worker restarted too many times; inspect worker logs.",
                                finished_at=utcnow(),
                            ):
                                removed = await _remove_cancelled_job(container, session, job_id)
                                return "STOPPED" if removed else "FAILED"
                            await container.progress_service(session).report(
                                job_id,
                                "orchestrator",
                                100,
                                "Job failed after repeated worker restarts.",
                                terminal=True,
                            )
                            return "FAILED"
                    container.settings = await RuntimeSettingsService(session, settings).resolve()
                    await check()
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
                        await check()
                    orchestrator = container.build_orchestrator(
                        session, upload_path_override=local_upload, cancellation_check=check
                    )
                    status = await orchestrator.run(job_id, claimed=True)
                except JobAlreadyClaimed:
                    logger.info("duplicate_conversion_delivery_skipped", job_id=job_id)
                    return "ALREADY_RUNNING"
                except JobCancellation:
                    removed = await _remove_cancelled_job(container, session, job_id)
                    return "STOPPED" if removed else "FAILED"
                except Exception as exc:
                    await session.rollback()
                    try:
                        await check()
                    except JobCancellation:
                        removed = await _remove_cancelled_job(container, session, job_id)
                        return "STOPPED" if removed else "FAILED"
                    if isinstance(exc, AppError):
                        logger.error("conversion_failed", job_id=job_id, error=exc.message)
                        message = exc.message
                    else:
                        logger.exception("conversion_crashed", job_id=job_id)
                        message = "Unexpected worker error"
                    if not await repository.finalize(
                        job_id, JobStatus.FAILED, error=message, finished_at=utcnow()
                    ):
                        removed = await _remove_cancelled_job(container, session, job_id)
                        return "STOPPED" if removed else "FAILED"
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
