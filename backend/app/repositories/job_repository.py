"""SQLAlchemy adapter for :class:`IJobRepository`."""

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Job, SearchDocument, utcnow
from app.domain.enums import JobStatus
from app.repositories.base import IJobRepository


class JobRepository(IJobRepository):
    """Unit-of-work style repository: each method commits its own change."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, job: Job) -> Job:
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def get(self, job_id: str) -> Job | None:
        statement = (
            select(Job)
            .options(selectinload(Job.stages), selectinload(Job.chapters))
            .where(Job.id == job_id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list(
        self, *, limit: int = 50, offset: int = 0, owner_id: int | None = None
    ) -> list[Job]:
        statement = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
        if owner_id is not None:
            statement = statement.where(Job.owner_id == owner_id)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def update_status(self, job_id: str, status: JobStatus, **fields: Any) -> Job | None:
        job = await self._session.get(Job, job_id)
        if job is None:
            return None
        job.status = status
        for key, value in fields.items():
            setattr(job, key, value)
        job.updated_at = utcnow()
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def claim(self, job_id: str) -> bool:
        result = await self._session.execute(
            update(Job)
            .where(
                Job.id == job_id, Job.status == JobStatus.QUEUED, Job.cancel_requested_at.is_(None)
            )
            .values(status=JobStatus.RUNNING, started_at=utcnow())
            .returning(Job.id)
        )
        changed = result.scalar_one_or_none() is not None
        await self._session.commit()
        return changed

    async def finalize(self, job_id: str, status: JobStatus, **fields: Any) -> bool:
        result = await self._session.execute(
            update(Job)
            .where(
                Job.id == job_id, Job.status == JobStatus.RUNNING, Job.cancel_requested_at.is_(None)
            )
            .values(status=status, **fields)
            .returning(Job.id)
        )
        changed = result.scalar_one_or_none() is not None
        await self._session.commit()
        return changed

    async def delete(self, job_id: str) -> bool:
        job = await self.get(job_id)
        if job is None:
            return False
        # Also clean SQLite test databases where foreign-key PRAGMA may be disabled.
        # Production's ON DELETE CASCADE remains the primary guarantee.
        await self._session.execute(delete(SearchDocument).where(SearchDocument.job_id == job_id))
        await self._session.delete(job)
        await self._session.commit()
        return True
