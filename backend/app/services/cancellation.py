"""Cooperative job cancellation: explicit signal that bypasses stage error capture."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job


class JobCancellation(Exception):
    """The owner requested removal; do not convert this into a FAILED stage."""


async def ensure_job_active(session: AsyncSession, job_id: str) -> None:
    row = (
        await session.execute(select(Job.cancel_requested_at, Job.status).where(Job.id == job_id))
    ).one_or_none()
    if row is None or row.cancel_requested_at is not None:
        raise JobCancellation()
