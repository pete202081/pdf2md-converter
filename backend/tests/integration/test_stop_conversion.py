"""Stop request authorization, atomic state race, and worker-side deletion."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.api.deps import get_container, get_session
from app.api.v1.routers.auth import COOKIE, digest, passwords
from app.config import Settings
from app.db.models import Base, Job, JobStage, ProgressEvent, User, UserSession, utcnow
from app.domain.enums import JobStatus, StageStatus
from app.main import create_app
from app.repositories.job_repository import JobRepository
from app.repositories.progress_repository import ProgressRepository
from app.worker.tasks import _remove_cancelled_job


async def test_stop_endpoint_owner_only_idempotent_and_terminal_race(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = fakeredis.aioredis.FakeRedis()
    settings = Settings()
    async with factory() as session:
        for uid, name, role in [(1, "alice", "USER"), (2, "admin", "ADMIN")]:
            session.add(
                User(
                    id=uid,
                    username=name,
                    role=role,
                    status="ACTIVE",
                    password_hash=passwords.hash("long-and-secret-passphrase"),
                )
            )
            session.add(
                UserSession(
                    user_id=uid,
                    token_hash=digest(f"token-{name}"),
                    csrf_token=f"csrf-{name}",
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
        session.add_all(
            [
                Job(
                    id="queued",
                    owner_id=1,
                    filename="a.pdf",
                    stored_path="unused",
                    status=JobStatus.QUEUED,
                ),
                Job(
                    id="running",
                    owner_id=1,
                    filename="b.pdf",
                    stored_path="unused",
                    status=JobStatus.RUNNING,
                ),
                Job(
                    id="done",
                    owner_id=1,
                    filename="c.pdf",
                    stored_path="unused",
                    status=JobStatus.SUCCEEDED,
                ),
            ]
        )
        await session.commit()
        app = create_app()
        app.dependency_overrides[get_container] = lambda: SimpleNamespace(
            redis=redis,
            settings=settings,
            job_repository=lambda s: JobRepository(s),
            progress_repository=lambda s: ProgressRepository(s),
        )

        async def override_session():
            yield session

        app.dependency_overrides[get_session] = override_session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/v1/jobs/queued/cancel")).status_code == 401
            client.cookies.set(COOKIE, "token-admin", path="/api/v1")
            assert (
                await client.post(
                    "/api/v1/jobs/queued/cancel", headers={"X-CSRF-Token": "csrf-admin"}
                )
            ).status_code == 404
            client.cookies.set(COOKIE, "token-alice", path="/api/v1")
            assert (await client.post("/api/v1/jobs/queued/cancel")).status_code == 403
            for jid in ("queued", "running"):
                for _ in range(2):
                    response = await client.post(
                        f"/api/v1/jobs/{jid}/cancel", headers={"X-CSRF-Token": "csrf-alice"}
                    )
                    assert response.status_code == 202, response.text
                state = (await client.get(f"/api/v1/jobs/{jid}")).json()
                assert state["cancel_requested_at"] is not None
                assert state["status"] in ("QUEUED", "RUNNING")
            assert (
                await client.post(
                    "/api/v1/jobs/done/cancel", headers={"X-CSRF-Token": "csrf-alice"}
                )
            ).status_code == 409
            assert await JobRepository(session).finalize("running", JobStatus.SUCCEEDED) is False
            assert await JobRepository(session).claim("queued") is False
    await engine.dispose()


async def test_worker_cleanup_local_and_remote_failure(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(upload_dir=str(tmp_path / "u"), output_dir=str(tmp_path / "o"))
    store = LocalFileStore(settings.upload_dir, settings.output_dir)

    async def chunks():
        yield b"%PDF-1.7\n"

    original = await store.save_upload("local", "book.pdf", chunks())
    output = await store.save_output_bytes("local", "book/chapter.md", b"# Chapter")
    archive = Path(settings.output_dir) / "local.zip"
    archive.write_bytes(b"partial")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    container = SimpleNamespace(
        settings=settings,
        redis=redis,
        object_store=None,
        job_repository=lambda s: JobRepository(s),
        progress_service=lambda s: SimpleNamespace(report=lambda *a, **kw: _return_none()),
    )
    async with factory() as session:
        session.add_all(
            [
                Job(
                    id="local",
                    filename="book.pdf",
                    stored_path=original,
                    cancel_requested_at=utcnow(),
                    status=JobStatus.RUNNING,
                ),
                Job(
                    id="remote",
                    filename="book.pdf",
                    stored_path="minio://missing",
                    storage_backend="minio",
                    cancel_requested_at=utcnow(),
                    status=JobStatus.RUNNING,
                ),
            ]
        )
        await session.commit()
        session.add_all(
            [
                JobStage(job_id="local", stage_name="extract_text", status=StageStatus.RUNNING),
                ProgressEvent(
                    job_id="local", stage="extract_text", percent=23, message="Processing"
                ),
            ]
        )
        await session.commit()
        assert await _remove_cancelled_job(container, session, "local") is True
        assert await JobRepository(session).get("local") is None
        assert not Path(original).exists() and not Path(output).exists() and not archive.exists()
        assert await _remove_cancelled_job(container, session, "remote") is False
        failed = await JobRepository(session).get("remote")
        assert failed is not None and failed.status == JobStatus.FAILED
        assert failed.error and "cleanup failed" in failed.error.lower()
    await engine.dispose()


async def _return_none() -> None:
    return None


async def test_worker_cleanup_removes_minio_job_prefix(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class Remote:
        removed: list[str] = []

        def key(self, owner_id: int, job_id: str, relative: str) -> str:
            return f"users/{owner_id}/jobs/{job_id}/{relative}"

        async def delete_prefix(self, prefix: str) -> None:
            self.removed.append(prefix)

    remote = Remote()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    container = SimpleNamespace(
        object_store=remote,
        redis=redis,
        job_repository=lambda s: JobRepository(s),
    )
    async with factory() as session:
        session.add(
            User(
                id=1,
                username="owner",
                role="USER",
                status="ACTIVE",
                password_hash="unused",
            )
        )
        session.add(
            Job(
                id="remote",
                owner_id=1,
                filename="book.pdf",
                stored_path="s3://bucket/source",
                storage_backend="minio",
                cancel_requested_at=utcnow(),
                status=JobStatus.RUNNING,
            )
        )
        await session.commit()
        assert await _remove_cancelled_job(container, session, "remote") is True
        assert remote.removed == ["users/1/jobs/remote/"]
        assert await JobRepository(session).get("remote") is None
        assert await _remove_cancelled_job(container, session, "remote") is True
    await engine.dispose()
