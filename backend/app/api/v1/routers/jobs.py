"""Job management endpoints."""

import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage.file_store import IFileStore
from app.api.deps import (
    get_container,
    get_file_store,
    get_job_repository,
    get_session,
    get_upload_service,
)
from app.api.schemas import ChapterOut, JobDetailOut, JobOut
from app.api.v1.routers.auth import check_owner, require_user
from app.container import Container
from app.db.models import User
from app.domain.enums import JobStatus
from app.domain.exceptions import ConflictError, NotFoundError, StorageError
from app.repositories.base import IJobRepository
from app.services.diagram_review import DiagramReviewService
from app.services.minio_diagram_review import MinioDiagramReviewService
from app.services.quality_review import QualityReviewService, content_hash
from app.services.rate_limits import RateLimiter
from app.services.upload_service import UploadService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

_FINISHED_STATES = (JobStatus.SUCCEEDED, JobStatus.PARTIAL)
_SAFE_ASSET_NAME = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpe?g|webp|gif|bmp|tiff?|jpx|jp2)$", re.IGNORECASE
)


@router.post("", status_code=201, response_model=JobOut)
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    container: Container = Depends(get_container),
    upload_service: UploadService = Depends(get_upload_service),
) -> JobOut:
    """Upload a PDF eBook and queue its conversion."""
    await RateLimiter(container.redis, container.settings).costly(request, "upload", user.id)
    job = await upload_service.accept_upload(file, owner_id=user.id)
    return JobOut.model_validate(job)


@router.get("", response_model=list[JobOut])
async def list_jobs(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
) -> list[JobOut]:
    """List conversion jobs, newest first."""
    jobs = await job_repository.list(
        limit=min(max(limit, 0), 200),
        offset=max(offset, 0),
        owner_id=None if user.role == "ADMIN" else user.id,
    )
    return [JobOut.model_validate(job) for job in jobs]


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: str,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
) -> JobDetailOut:
    """Get a job with its stage breakdown and chapter manifest."""
    job = check_owner(await job_repository.get(job_id), user)
    return JobDetailOut.model_validate(job)


@router.get("/{job_id}/chapters", response_model=list[ChapterOut])
async def list_chapters(
    job_id: str,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
) -> list[ChapterOut]:
    """List the generated chapters of a job in order."""
    job = check_owner(await job_repository.get(job_id), user)
    return [ChapterOut.model_validate(chapter) for chapter in job.chapters]


@router.get("/{job_id}/chapters/{order_index}/preview", response_class=PlainTextResponse)
async def preview_chapter(
    job_id: str,
    order_index: int,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> PlainTextResponse:
    """Return the raw Markdown of one chapter (for the preview pane)."""
    job = check_owner(await job_repository.get(job_id), user)
    if job.storage_backend == "minio" and job.status not in _FINISHED_STATES:
        raise NotFoundError("Chapter is not available yet")
    chapter = next((c for c in job.chapters if c.order_index == order_index), None)
    if chapter is None or not chapter.md_path:
        raise NotFoundError("Chapter not found or not generated yet")
    if job.storage_backend == "minio":
        if not container.object_store:
            raise StorageError("MinIO storage is not configured")
        if not await container.object_store.exists(chapter.md_path):
            raise NotFoundError("Chapter file is not available")
        return PlainTextResponse(await container.object_store.read_text(chapter.md_path))
    if not await file_store.exists(chapter.md_path):
        raise NotFoundError("Chapter file is not available")
    return PlainTextResponse(await file_store.read_text(chapter.md_path))


@router.get("/{job_id}/assets/{asset_name}")
async def get_job_asset(
    job_id: str,
    asset_name: str,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> Response:
    """Serve a generated image for chapter Markdown previews (no arbitrary paths)."""
    if not _SAFE_ASSET_NAME.fullmatch(asset_name):
        raise NotFoundError("Asset not found")
    job = check_owner(await job_repository.get(job_id), user)
    if job.storage_backend == "minio":
        if job.status not in _FINISHED_STATES:
            raise NotFoundError("Asset is not available yet")
        remote = container.object_store
        if not remote:
            raise StorageError("MinIO storage is not configured")
        uri = remote.uri(remote.key(job.owner_id, job.id, f"output/assets/{asset_name}"))
        if not await remote.exists(uri):
            raise NotFoundError("Asset not found")
        return StreamingResponse(
            await remote.stream(uri),
            media_type=mimetypes.guess_type(asset_name)[0] or "image/png",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
        )
    path = file_store.get_output_path(job_id, f"assets/{asset_name}")
    if not await file_store.exists(path):
        raise NotFoundError("Asset not found")
    return FileResponse(
        path,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@router.get("/{job_id}/download")
async def download_job(
    job_id: str,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> Response:
    """Download the converted chapter tree (ordered folders + manifest) as a ZIP."""
    job = check_owner(await job_repository.get(job_id), user)
    if job.status not in _FINISHED_STATES:
        raise ConflictError(
            "Job has not finished successfully yet",
            details={"status": job.status.value},
        )
    download_name = f"{Path(job.filename).stem}_markdown.zip"
    if job.storage_backend == "minio":
        remote = container.object_store
        if not remote:
            raise StorageError("MinIO storage is not configured")
        if not job.output_path or not await remote.exists(job.output_path):
            raise NotFoundError("No downloadable output is available for this job")
        return StreamingResponse(
            await remote.stream(job.output_path),
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(download_name)}",
                "Cache-Control": "private, no-store",
            },
        )
    if not job.output_path or not await file_store.exists(job.output_path):
        raise NotFoundError("No downloadable output is available for this job")
    return FileResponse(job.output_path, media_type="application/zip", filename=download_name)


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: str,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> Response:
    """Delete a job, its database records and its stored files."""
    job = check_owner(await job_repository.get(job_id), user)
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        raise ConflictError(
            "Cannot delete a job that is queued or running",
            details={"status": job.status.value},
        )
    if job.storage_backend == "minio":
        remote = container.object_store
        if not remote:
            raise StorageError("MinIO storage is not configured")
        await remote.delete_prefix(
            remote.key(job.owner_id, job.id, "upload").rsplit("/", 1)[0] + "/"
        )
    else:
        await file_store.delete_job_files(job_id)
    await job_repository.delete(job_id)
    return Response(status_code=204)


class ReviewDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ACCEPTED_IMAGE", "EDITED_MERMAID"]
    mermaid_code: str | None = Field(default=None, max_length=8000)


class ReviewItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    chapter_order: int
    page_number: int
    occurrence_index: int
    asset_name: str
    reason: str
    decision: str
    mermaid_code: str | None
    reviewed_at: datetime | None


@router.get("/{job_id}/reviews", response_model=list[ReviewItemOut])
async def list_diagram_reviews(
    job_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
) -> list[ReviewItemOut]:
    from app.repositories.job_repository import JobRepository

    check_owner(await JobRepository(session).get(job_id), user)
    items = await DiagramReviewService(session, file_store).list_for_job(job_id)
    return [ReviewItemOut.model_validate(item) for item in items]


@router.put(
    "/{job_id}/reviews/{item_id}",
    response_model=ReviewItemOut,
)
async def decide_diagram_review(
    job_id: str,
    item_id: str,
    body: ReviewDecisionIn,
    request: Request,
    user: User = Depends(require_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
) -> ReviewItemOut:
    from app.repositories.job_repository import JobRepository

    job = check_owner(await JobRepository(session).get(job_id), user)
    await RateLimiter(container.redis, container.settings).costly(request, "review", user.id)
    if job.storage_backend == "minio":
        if not container.object_store:
            raise StorageError("MinIO storage is not configured")
        item = await MinioDiagramReviewService(session, container.object_store).decide(
            job_id, item_id, body.decision, body.mermaid_code
        )
    else:
        item = await DiagramReviewService(session, file_store).decide(
            job_id, item_id, body.decision, body.mermaid_code
        )
    return ReviewItemOut.model_validate(item)


class ChapterEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    markdown: str = Field(min_length=1, max_length=2 * 1024 * 1024)


class UndoEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def quality_service(
    session: AsyncSession, file_store: IFileStore, container: Container
) -> QualityReviewService:
    return QualityReviewService(session, file_store, getattr(container, "object_store", None))


@router.get("/{job_id}/quality")
async def quality_summary(
    job_id: str,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    job = check_owner(await job_repository.get(job_id), user)
    return await quality_service(session, file_store, container).summary(job)


@router.put("/{job_id}/quality/pages/{page_number}")
async def mark_quality_page(
    job_id: str,
    page_number: int,
    request: Request,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    job = check_owner(await job_repository.get(job_id), user)
    await RateLimiter(container.redis, container.settings).costly(request, "review", user.id)
    return await quality_service(session, file_store, container).mark_page(
        job, page_number, user.id
    )


@router.get("/{job_id}/quality/chapters/{order_index}/pages/{page_number}.png")
async def source_page_image(
    job_id: str,
    order_index: int,
    page_number: int,
    request: Request,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> Response:
    job = check_owner(await job_repository.get(job_id), user)
    await RateLimiter(container.redis, container.settings).costly(request, "page", user.id)
    image = await quality_service(session, file_store, container).page_image(
        job, order_index, page_number
    )
    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{job_id}/quality/chapters/{order_index}")
async def edit_source(
    job_id: str,
    order_index: int,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> dict[str, str | bool]:
    job = check_owner(await job_repository.get(job_id), user)
    if job.status not in _FINISHED_STATES:
        raise ConflictError("Only completed jobs can be edited")
    chapter = next((c for c in job.chapters if c.order_index == order_index), None)
    if not chapter or not chapter.md_path:
        raise NotFoundError("Chapter is unavailable")
    service = quality_service(session, file_store, container)
    text = await service._text(job, chapter.md_path)
    digest_value = content_hash(text)
    from sqlalchemy import select

    from app.db.models import ChapterEdit

    last_edit = await session.scalar(
        select(ChapterEdit)
        .where(
            ChapterEdit.job_id == job_id,
            ChapterEdit.chapter_order == order_index,
            ChapterEdit.undone_at.is_(None),
        )
        .order_by(ChapterEdit.id.desc())
        .limit(1)
    )
    return {
        "markdown": text,
        "hash": digest_value,
        "can_undo": bool(last_edit and last_edit.new_hash == digest_value),
    }


@router.put("/{job_id}/quality/chapters/{order_index}")
async def edit_quality_chapter(
    job_id: str,
    order_index: int,
    body: ChapterEditIn,
    request: Request,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> dict[str, str]:
    check_owner(await job_repository.get(job_id), user)
    await RateLimiter(container.redis, container.settings).costly(request, "review", user.id)
    return await quality_service(session, file_store, container).edit(
        job_id, order_index, body.expected_hash, body.markdown, user.id
    )


@router.post("/{job_id}/quality/chapters/{order_index}/undo")
async def undo_quality_chapter(
    job_id: str,
    order_index: int,
    body: UndoEditIn,
    request: Request,
    user: User = Depends(require_user),
    job_repository: IJobRepository = Depends(get_job_repository),
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
    container: Container = Depends(get_container),
) -> dict[str, str]:
    check_owner(await job_repository.get(job_id), user)
    await RateLimiter(container.redis, container.settings).costly(request, "review", user.id)
    return await quality_service(session, file_store, container).undo(
        job_id, order_index, body.expected_hash, user.id
    )
