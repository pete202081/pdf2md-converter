"""SQLAlchemy adapter for :class:`IStageRepository`."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobStage
from app.domain.enums import StageStatus
from app.repositories.base import IStageRepository


class StageRepository(IStageRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, job_id: str, stage_name: str, status: StageStatus) -> JobStage:
        stage = JobStage(job_id=job_id, stage_name=stage_name, status=status, metrics={})
        self._session.add(stage)
        await self._session.commit()
        await self._session.refresh(stage)
        return stage

    async def mark(self, stage_id: int, status: StageStatus, **fields: Any) -> JobStage | None:
        stage = await self._session.get(JobStage, stage_id)
        if stage is None:
            return None
        stage.status = status
        for key, value in fields.items():
            setattr(stage, key, value)
        await self._session.commit()
        await self._session.refresh(stage)
        return stage
