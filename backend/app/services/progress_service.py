"""Progress reporting service.

Every progress update is (a) persisted to the database so late-joining clients
can replay history, and (b) published to a Redis pub/sub channel so connected
clients receive it live over SSE. Failures here must never break a conversion,
so they are logged and swallowed.
"""

import json

import redis.asyncio as aioredis

from app.db.models import ProgressEvent
from app.logging import get_logger
from app.repositories.base import IProgressRepository


def events_channel(job_id: str) -> str:
    """Redis pub/sub channel name for a job's progress events."""
    return f"job:{job_id}:events"


class ProgressService:
    """Publishes progress events to the DB and to Redis pub/sub."""

    def __init__(self, repository: IProgressRepository, redis: aioredis.Redis) -> None:
        self._repository = repository
        self._redis = redis
        self._logger = get_logger("ProgressService")

    async def report(
        self, job_id: str, stage: str, percent: int, message: str, *, terminal: bool = False
    ) -> None:
        """Record and broadcast a progress update."""
        event = ProgressEvent(job_id=job_id, stage=stage, percent=percent, message=message)
        try:
            await self._repository.add(event)
        except Exception:
            self._logger.exception("progress_db_write_failed", job_id=job_id, stage=stage)

        payload = {
            "job_id": job_id,
            "stage": stage,
            "percent": percent,
            "message": message,
            "ts": event.ts.isoformat() if event.ts else None,
            "terminal": terminal,
        }
        try:
            await self._redis.publish(events_channel(job_id), json.dumps(payload))
        except Exception:
            self._logger.exception("progress_publish_failed", job_id=job_id, stage=stage)
