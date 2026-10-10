"""Job management endpoints."""

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage.file_store import IFileStore
from app.api.deps import (
    get_file_store,
    get_job_repository,
    get_session,
    get_upload_service,
)
from app.api.schemas import ChapterOut, JobDetailOut, JobOut
from app.api.v1.routers.admin import require_admin
from app.domain.enums import JobStatus
from app.domain.exceptions import ConflictError, NotFoundError
from app.repositories.base import IJobRepository
from app.services.diagram_review import DiagramReviewService
from app.services.upload_service import UploadService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

_FINISHED_STATES = (JobStatus.SUCCEEDED, JobStatus.PARTIAL)
_SAFE_ASSET_NAME = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpe?g|webp|gif|bmp|tiff?|jpx|jp2)$", re.IGNORECASE
)


@router.post("", status_code=201, response_model=JobOut)
async def create_job(
    file: UploadFile = File(...),
    upload_service: UploadService = Depends(get_upload_service),
) -> JobOut:
    """Upload a PDF eBook and queue its conversion."""
    job = await upload_service.accept_upload(file)
    return JobOut.model_validate(job)


@router.get("", response_model=list[JobOut])
async def list_jobs(
    limit: int = 50,
    offset: int = 0,
    job_repository: IJobRepository = Depends(get_job_repository),
) -> list[JobOut]:
    """List conversion jobs, newest first."""
    jobs = await job_repository.list(limit=min(limit, 200), offset=offset)
    return [JobOut.model_validate(job) for job in jobs]


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: str,
    job_repository: IJobRepository = Depends(get_job_repository),
) -> JobDetailOut:
    """Get a job with its stage breakdown and chapter manifest."""
    job = await job_repository.get(job_id)
    if job is None:
        raise NotFoundError(f"Job '{job_id}' not found")
    return JobDetailOut.model_validate(job)


@router.get("/{job_id}/chapters", response_model=list[ChapterOut])
async def list_chapters(
    job_id: str,
    job_repository: IJobRepository = Depends(get_job_repository),
) -> list[ChapterOut]:
    """List the generated chapters of a job in order."""
    job = await job_repository.get(job_id)
    if job is None:
        raise NotFoundError(f"Job '{job_id}' not found")
    return [ChapterOut.model_validate(chapter) for chapter in job.chapters]


@router.get("/{job_id}/chapters/{order_index}/preview", response_class=PlainTextResponse)
async def preview_chapter(
    job_id: str,
    order_index: int,
    job_repository: IJobRepository = Depends(get_job_repository),
    file_store: IFileStore = Depends(get_file_store),
) -> PlainTextResponse:
    """Return the raw Markdown of one chapter (for the preview pane)."""
    job = await job_repository.get(job_id)
    if job is None:
        raise NotFoundError(f"Job '{job_id}' not found")
    chapter = next((c for c in job.chapters if c.order_index == order_index), None)
    if chapter is None or not chapter.md_path:
        raise NotFoundError("Chapter not found or not generated yet")
    if not await file_store.exists(chapter.md_path):
        raise NotFoundError("Chapter file is not available")
    return PlainTextResponse(await file_store.read_text(chapter.md_path))


@router.get("/{job_id}/assets/{asset_name}")
async def get_job_asset(
    job_id: str,
    asset_name: str,
    job_repository: IJobRepository = Depends(get_job_repository),
    file_store: IFileStore = Depends(get_file_store),
) -> FileResponse:
    """Serve a generated image for chapter Markdown previews (no arbitrary paths)."""
    if not _SAFE_ASSET_NAME.fullmatch(asset_name):
        raise NotFoundError("Asset not found")
    job = await job_repository.get(job_id)
    if job is None:
        raise NotFoundError(f"Job '{job_id}' not found")
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
    job_repository: IJobRepository = Depends(get_job_repository),
    file_store: IFileStore = Depends(get_file_store),
) -> FileResponse:
    """Download the converted chapter tree (ordered folders + manifest) as a ZIP."""
    job = await job_repository.get(job_id)
    if job is None:
        raise NotFoundError(f"Job '{job_id}' not found")
    if job.status not in _FINISHED_STATES:
        raise ConflictError(
            "Job has not finished successfully yet",
            details={"status": job.status.value},
        )
    if not job.output_path or not await file_store.exists(job.output_path):
        raise NotFoundError("No downloadable output is available for this job")
    download_name = f"{Path(job.filename).stem}_markdown.zip"
    return FileResponse(job.output_path, media_type="application/zip", filename=download_name)


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: str,
    job_repository: IJobRepository = Depends(get_job_repository),
    file_store: IFileStore = Depends(get_file_store),
) -> Response:
    """Delete a job, its database records and its stored files."""
    job = await job_repository.get(job_id)
    if job is None:
        raise NotFoundError(f"Job '{job_id}' not found")
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        raise ConflictError(
            "Cannot delete a job that is queued or running",
            details={"status": job.status.value},
        )
    await job_repository.delete(job_id)
    await file_store.delete_job_files(job_id)
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
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
) -> list[ReviewItemOut]:
    items = await DiagramReviewService(session, file_store).list_for_job(job_id)
    return [ReviewItemOut.model_validate(item) for item in items]


@router.put(
    "/{job_id}/reviews/{item_id}",
    response_model=ReviewItemOut,
    dependencies=[Depends(require_admin)],
)
async def decide_diagram_review(
    job_id: str,
    item_id: str,
    body: ReviewDecisionIn,
    session: AsyncSession = Depends(get_session),
    file_store: IFileStore = Depends(get_file_store),
) -> ReviewItemOut:
    item = await DiagramReviewService(session, file_store).decide(
        job_id, item_id, body.decision, body.mermaid_code
    )
    return ReviewItemOut.model_validate(item)
