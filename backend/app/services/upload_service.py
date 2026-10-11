"""Upload use-case: validate, store, persist job, enqueue conversion."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable

import aiofiles
from fastapi import UploadFile

from app.adapters.storage.file_store import IFileStore, sanitize_filename
from app.adapters.storage.minio_store import MinioObjectStore
from app.config import Settings
from app.db.models import Job, utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import AppError, ProcessingError, ValidationError
from app.logging import get_logger
from app.repositories.base import IJobRepository
from app.services.pdf_utils import count_pdf_pages

_CHUNK_SIZE = 1024 * 1024  # 1 MB
_PDF_MAGIC = b"%PDF-"


class UploadService:
    """Accepts an uploaded PDF, validates it, and queues a conversion job.

    Dependencies are injected (Dependency Inversion): the file store, the job
    repository, settings, and an ``enqueue`` callable — which makes the service
    fully unit-testable without Celery or a database.
    """

    def __init__(
        self,
        file_store: IFileStore,
        job_repository: IJobRepository,
        settings: Settings,
        enqueue: Callable[[str], None],
        object_store: MinioObjectStore | None = None,
    ) -> None:
        self._file_store = file_store
        self._job_repository = job_repository
        self._settings = settings
        self._enqueue = enqueue
        self._object_store = object_store
        self._logger = get_logger("UploadService")

    @property
    def max_upload_bytes(self) -> int:
        return self._settings.max_upload_bytes

    async def accept_upload(
        self,
        file: UploadFile,
        *,
        owner_id: int = 1,
        source: tuple[str, str, str] | None = None,
    ) -> Job:
        """Validate and store the upload, create the job row, enqueue the task.

        Raises:
            ValidationError: for any client-fixable problem (type, size, pages).
            ProcessingError: if the job could not be queued.
        """
        filename = file.filename or "upload.pdf"
        if not filename.lower().endswith(".pdf"):
            raise ValidationError("Only PDF files are supported", details={"filename": filename})

        job_id = uuid.uuid4().hex
        size = 0

        async def chunks() -> AsyncIterator[bytes]:
            nonlocal size
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > self._settings.max_upload_bytes:
                    raise ValidationError(
                        "Uploaded file exceeds the maximum allowed size",
                        details={"max_bytes": self._settings.max_upload_bytes},
                    )
                yield chunk

        try:
            stored_path = await self._file_store.save_upload(job_id, filename, chunks())
            if not await self._has_pdf_magic(stored_path):
                raise ValidationError("The uploaded file is not a valid PDF (missing PDF header)")
            page_count = await asyncio.to_thread(count_pdf_pages, stored_path)
            if page_count > self._settings.max_pages:
                raise ValidationError(
                    "PDF exceeds the maximum supported page count",
                    details={"page_count": page_count, "max_pages": self._settings.max_pages},
                )
            if self._settings.storage_backend == "minio":
                if self._object_store is None:
                    raise ProcessingError("MinIO storage is not configured")
                key = self._object_store.key(
                    owner_id, job_id, f"upload/{sanitize_filename(filename)}"
                )
                stored_path = await self._object_store.upload(stored_path, key)
                await self._file_store.delete_job_files(job_id)
        except AppError:
            # Never leave orphaned files/objects behind on validation or upload failure.
            await self._file_store.delete_job_files(job_id)
            if self._settings.storage_backend == "minio" and self._object_store:
                try:
                    await self._object_store.delete_prefix(f"{owner_id}/{job_id}/")
                except AppError:
                    self._logger.warning("upload_cleanup_failed", job_id=job_id)
            raise

        job = Job(
            id=job_id,
            owner_id=owner_id,
            storage_backend=self._settings.storage_backend,
            filename=filename,
            source_type="GITHUB" if source else "UPLOAD",
            source_repo=source[0] if source else None,
            source_path=source[1] if source else None,
            source_revision=source[2] if source else None,
            stored_path=stored_path,
            status=JobStatus.QUEUED,
            page_count=page_count,
        )
        try:
            job = await self._job_repository.create(job)
        except Exception:
            if self._settings.storage_backend == "minio" and self._object_store:
                await self._object_store.delete_prefix(f"{owner_id}/{job_id}/")
            else:
                await self._file_store.delete_job_files(job_id)
            raise

        try:
            self._enqueue(job.id)
        except Exception as exc:
            await self._job_repository.update_status(
                job_id,
                JobStatus.FAILED,
                error="Failed to enqueue conversion job",
                finished_at=utcnow(),
            )
            raise ProcessingError("Could not queue the conversion job — please try again") from exc

        self._logger.info("job_accepted", job_id=job.id, filename=filename, page_count=page_count)
        return job

    @staticmethod
    async def _has_pdf_magic(path: str, head_size: int = 1024) -> bool:
        """Check for the %PDF- header within the first KB (per PDF spec)."""
        async with aiofiles.open(path, "rb") as handle:
            head = await handle.read(head_size)
        return _PDF_MAGIC in head
