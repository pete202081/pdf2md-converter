"""SQLAlchemy adapter for :class:`IProgressRepository`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProgressEvent
from app.repositories.base import IProgressRepository


class ProgressRepository(IProgressRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: ProgressEvent) -> ProgressEvent:
        self._session.add(event)
        await self._session.commit()
        await self._session.refresh(event)
        return event

    async def recent(self, job_id: str, *, limit: int = 100) -> list[ProgressEvent]:
        statement = (
            select(ProgressEvent)
            .where(ProgressEvent.job_id == job_id)
            .order_by(ProgressEvent.ts.desc(), ProgressEvent.id.desc())
            .limit(limit)
        )
        result = await self._session.execute(statement)
        return list(reversed(result.scalars().all()))
