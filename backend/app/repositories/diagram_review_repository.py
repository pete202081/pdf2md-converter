"""SQLAlchemy adapter for persisted diagram fallback occurrences."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DiagramReviewItem
from app.repositories.base import IDiagramReviewRepository


class DiagramReviewRepository(IDiagramReviewRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, item: DiagramReviewItem) -> DiagramReviewItem:
        self._session.add(item)
        await self._session.commit()
        await self._session.refresh(item)
        return item
