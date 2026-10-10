"""SQLAlchemy adapter for :class:`IChapterRepository`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chapter
from app.repositories.base import IChapterRepository


class ChapterRepository(IChapterRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, chapter: Chapter) -> Chapter:
        self._session.add(chapter)
        await self._session.commit()
        await self._session.refresh(chapter)
        return chapter

    async def list_for_job(self, job_id: str) -> list[Chapter]:
        statement = (
            select(Chapter).where(Chapter.job_id == job_id).order_by(Chapter.order_index.asc())
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())
