"""Authenticated GitHub PDF discovery; admin-only private repository access."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.github_pdf import GitHubPDF
from app.api.deps import get_container, get_session, get_upload_service
from app.api.schemas import JobOut
from app.api.v1.routers.auth import require_admin, require_user
from app.container import Container
from app.db.models import User
from app.domain.exceptions import NotFoundError
from app.services.rate_limits import RateLimiter
from app.services.runtime_settings import RuntimeSettingsService
from app.services.upload_service import UploadService

router = APIRouter(prefix="/api/v1/github", tags=["github-pdfs"])


async def get_github(
    session: AsyncSession = Depends(get_session), container: Container = Depends(get_container)
) -> GitHubPDF:
    settings = await RuntimeSettingsService(session, container.settings).resolve()
    return GitHubPDF(settings.github_pat)


async def throttle(
    request: Request, container: Container, user: User, *, import_pdf: bool = False
) -> None:
    limiter = RateLimiter(container.redis, container.settings)
    if import_pdf:
        await limiter.costly(request, "upload", user.id)
    else:
        await limiter.check("github:ip", limiter.remote_ip(request), limit=100, window=3600)
        await limiter.check("github:user", str(user.id), limit=40, window=3600)


@router.get("/search")
async def search_pdf_repositories(
    request: Request,
    query: str,
    page: int = 1,
    user: User = Depends(require_user),
    container: Container = Depends(get_container),
    github: GitHubPDF = Depends(get_github),
) -> dict[str, Any]:
    await throttle(request, container, user)
    return await github.search(query, page)


class BrowseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(min_length=3, max_length=201)
    directory: str = Field(default="", max_length=900)


@router.post("/browse")
async def browse_repository(
    body: BrowseRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    container: Container = Depends(get_container),
    github: GitHubPDF = Depends(get_github),
) -> dict[str, Any]:
    await throttle(request, container, user)
    response.headers["Cache-Control"] = "private, no-store"
    return await github.browse(body.repo, body.directory, admin=user.role == "ADMIN")


@router.get("/private-repositories")
async def private_repositories(
    request: Request,
    response: Response,
    page: int = 1,
    user: User = Depends(require_admin),
    container: Container = Depends(get_container),
    github: GitHubPDF = Depends(get_github),
) -> list[dict[str, Any]]:
    await throttle(request, container, user)
    response.headers["Cache-Control"] = "private, no-store"
    return await github.private_repositories(page)


class GitHubSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(min_length=3, max_length=201)
    path: str = Field(min_length=5, max_length=900)
    revision: str = Field(min_length=40, max_length=40)
    sha: str = Field(min_length=40, max_length=40)


@router.post("/convert", status_code=201, response_model=JobOut)
async def convert_github_pdf(
    body: GitHubSelection,
    request: Request,
    user: User = Depends(require_user),
    container: Container = Depends(get_container),
    github: GitHubPDF = Depends(get_github),
    upload: UploadService = Depends(get_upload_service),
) -> JobOut:
    await throttle(request, container, user, import_pdf=True)
    info = await github.repository(body.repo, admin=user.role == "ADMIN")
    if info["private"] and user.role != "ADMIN":
        raise NotFoundError("GitHub repository or file is unavailable")
    file = await github.download(
        body.repo, body.path, body.revision, body.sha, upload.max_upload_bytes
    )
    try:
        job = await upload.accept_upload(
            file,
            owner_id=user.id,
            source=(str(info["full_name"]), body.path, body.revision),
        )
        return JobOut.model_validate(job)
    finally:
        await file.close()
