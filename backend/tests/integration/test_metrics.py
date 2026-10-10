"""Prometheus endpoint uses only bounded labels and is not part of public nginx proxy."""

from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_container, get_session
from app.db.models import Base
from app.main import create_app


async def test_metrics_templates_and_internal_aggregates(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.hset("pdf2md:metrics:stage_count", "convert_mermaid", 2)
    async with factory() as session:
        app = create_app()
        app.dependency_overrides[get_container] = lambda: SimpleNamespace(redis=redis)

        async def override_session():
            yield session

        app.dependency_overrides[get_session] = override_session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/jobs/sensitive-book-id")
            assert response.status_code == 401
            metrics = await client.get("/metrics")
            assert metrics.status_code == 200
            assert 'route="/api/v1/jobs/{job_id}"' in metrics.text
            assert "sensitive-book-id" not in metrics.text
            assert "pdf2md_queue_depth" in metrics.text
            assert (
                'pdf2md_stage_duration_seconds_count{stage="convert_mermaid"} 2.0' in metrics.text
            )
    await redis.aclose()
    await engine.dispose()
