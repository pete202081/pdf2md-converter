"""Tests for the upload use-case (validation, storage, job creation, enqueue)."""

import io
from pathlib import Path

import pymupdf
import pytest
from fastapi import UploadFile

from app.adapters.storage.file_store import LocalFileStore
from app.config import Settings
from app.domain.enums import JobStatus
from app.domain.exceptions import ValidationError
from app.services.upload_service import UploadService
from tests.fakes import InMemoryJobRepository


def make_pdf_bytes(pages: int = 1) -> bytes:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"Page {index + 1}")
    data = document.tobytes()
    document.close()
    return data


def make_upload(data: bytes, filename: str = "book.pdf") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def make_service(
    tmp_path: Path,
    settings: Settings | None = None,
) -> tuple[UploadService, InMemoryJobRepository, list[str]]:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    repository = InMemoryJobRepository()
    enqueued: list[str] = []
    service = UploadService(
        file_store=store,
        job_repository=repository,
        settings=settings or Settings(),
        enqueue=enqueued.append,
    )
    return service, repository, enqueued


async def test_accept_valid_pdf_creates_queued_job_and_enqueues(tmp_path: Path) -> None:
    service, repository, enqueued = make_service(tmp_path)

    job = await service.accept_upload(make_upload(make_pdf_bytes(pages=2), "My Book.pdf"))

    assert job.status == JobStatus.QUEUED
    assert job.page_count == 2
    assert job.filename == "My Book.pdf"
    assert enqueued == [job.id]
    assert repository.jobs[job.id] is job
    # File really landed on disk under the job's upload directory
    assert Path(job.stored_path).read_bytes().startswith(b"%PDF-")
    assert Path(job.stored_path).parent.name == job.id


async def test_rejects_non_pdf_extension(tmp_path: Path) -> None:
    service, repository, enqueued = make_service(tmp_path)

    with pytest.raises(ValidationError) as exc_info:
        await service.accept_upload(make_upload(make_pdf_bytes(), "book.txt"))

    assert "Only PDF" in exc_info.value.message
    assert repository.jobs == {}
    assert enqueued == []


async def test_rejects_non_pdf_content_and_cleans_up(tmp_path: Path) -> None:
    service, repository, _ = make_service(tmp_path)

    with pytest.raises(ValidationError) as exc_info:
        await service.accept_upload(make_upload(b"hello world, not a pdf"))

    assert "not a valid PDF" in exc_info.value.message
    assert repository.jobs == {}
    # No orphaned files left behind
    uploads_root = tmp_path / "uploads"
    leftovers = list(uploads_root.iterdir()) if uploads_root.exists() else []
    assert leftovers == []


async def test_rejects_oversized_upload(tmp_path: Path) -> None:
    settings = Settings(max_upload_bytes=16)  # tiny limit on purpose
    service, repository, _ = make_service(tmp_path, settings=settings)

    with pytest.raises(ValidationError) as exc_info:
        await service.accept_upload(make_upload(make_pdf_bytes()))

    assert "maximum allowed size" in exc_info.value.message
    assert repository.jobs == {}


async def test_rejects_too_many_pages(tmp_path: Path) -> None:
    settings = Settings(max_pages=1)
    service, repository, _ = make_service(tmp_path, settings=settings)

    with pytest.raises(ValidationError) as exc_info:
        await service.accept_upload(make_upload(make_pdf_bytes(pages=2)))

    assert "page count" in exc_info.value.message
    assert repository.jobs == {}
