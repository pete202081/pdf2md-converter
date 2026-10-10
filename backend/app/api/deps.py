"""FastAPI dependency providers.

These are the only place where the framework meets the composition root —
everything downstream stays framework-agnostic and testable.
"""

from collections.abc import AsyncIterator
from typing import cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage.file_store import IFileStore
from app.container import Container
from app.repositories.base import (
    IChapterRepository,
    IJobRepository,
    IProgressRepository,
)
from app.services.runtime_settings import RuntimeSettingsService
from app.services.upload_service import UploadService


def get_container(request: Request) -> Container:
    """Return the container stored on the app by the lifespan handler."""
    return cast(Container, request.app.state.container)


async def get_session(
    container: Container = Depends(get_container),
) -> AsyncIterator[AsyncSession]:
    """Yield a per-request database session."""
    async with container.database.session() as session:
        yield session


def get_file_store(container: Container = Depends(get_container)) -> IFileStore:
    return container.file_store


def get_job_repository(
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> IJobRepository:
    return container.job_repository(session)


def get_chapter_repository(
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> IChapterRepository:
    return container.chapter_repository(session)


def get_progress_repository(
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> IProgressRepository:
    return container.progress_repository(session)


async def get_upload_service(
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> UploadService:
    settings = await RuntimeSettingsService(session, container.settings).resolve()
    return container.upload_service(session, settings=settings)
