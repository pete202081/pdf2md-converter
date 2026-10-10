"""Tests for the progress reporting service (DB + Redis fan-out)."""

import json

import fakeredis.aioredis
import pytest

from app.db.models import ProgressEvent
from app.repositories.base import IProgressRepository
from app.services.progress_service import ProgressService, events_channel
from tests.fakes import InMemoryProgressRepository
from tests.helpers import next_pubsub_message


class FailingProgressRepository(IProgressRepository):
    async def add(self, event: ProgressEvent) -> ProgressEvent:
        raise RuntimeError("database is down")

    async def recent(self, job_id: str, *, limit: int = 100) -> list[ProgressEvent]:
        return []


async def test_report_persists_event_and_publishes_to_redis() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(events_channel("job-1"))
    repository = InMemoryProgressRepository()
    service = ProgressService(repository, redis)

    await service.report("job-1", "validate_upload", 10, "working")

    assert len(repository.events) == 1
    event = repository.events[0]
    assert event.job_id == "job-1"
    assert event.stage == "validate_upload"
    assert event.percent == 10
    assert event.message == "working"

    message = await next_pubsub_message(pubsub)
    assert message is not None
    payload = json.loads(message["data"])
    assert payload["job_id"] == "job-1"
    assert payload["stage"] == "validate_upload"
    assert payload["percent"] == 10
    assert payload["terminal"] is False

    await pubsub.unsubscribe(events_channel("job-1"))
    await pubsub.aclose()


async def test_terminal_flag_is_propagated() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(events_channel("job-2"))
    service = ProgressService(InMemoryProgressRepository(), redis)

    await service.report("job-2", "orchestrator", 100, "done", terminal=True)

    message = await next_pubsub_message(pubsub)
    assert message is not None
    assert json.loads(message["data"])["terminal"] is True

    await pubsub.unsubscribe(events_channel("job-2"))
    await pubsub.aclose()


async def test_report_never_raises_even_if_infrastructure_fails() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    service = ProgressService(FailingProgressRepository(), redis)

    # Must not raise — progress reporting must never break a conversion.
    await service.report("job-3", "stage", 50, "still going")


async def test_channel_name_is_per_job() -> None:
    assert events_channel("abc") == "job:abc:events"
    assert events_channel("abc") != events_channel("def")


@pytest.mark.parametrize("percent", [0, 50, 100])
async def test_percent_is_stored_as_int(percent: int) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    repository = InMemoryProgressRepository()
    service = ProgressService(repository, redis)

    await service.report("job-4", "stage", percent, "msg")

    assert repository.events[0].percent == percent
