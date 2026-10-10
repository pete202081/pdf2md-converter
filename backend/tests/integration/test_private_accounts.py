"""HTTP regression: pending users, CSRF, ownership and admin lifecycle."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.api.deps import get_container, get_file_store, get_job_repository, get_session
from app.api.v1.routers.auth import COOKIE, digest, passwords
from app.config import Settings
from app.db.models import Base, Job, PasswordResetCode, User, UserSession, utcnow
from app.domain.enums import JobStatus
from app.main import create_app
from app.repositories.job_repository import JobRepository
from app.repositories.progress_repository import ProgressRepository


async def test_private_job_access_and_approval(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    redis = fakeredis.aioredis.FakeRedis()
    async with factory() as session:
        session.add(
            User(
                id=1,
                username="admin",
                role="ADMIN",
                status="ACTIVE",
                password_hash=passwords.hash("admin-passphrase-long"),
            )
        )
        session.add(
            UserSession(
                user_id=1,
                token_hash=digest("admin-test-token"),
                csrf_token="admin-csrf",
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        await session.commit()
        app = create_app()
        container = SimpleNamespace(
            redis=redis,
            settings=Settings(),
            job_repository=lambda s: JobRepository(s),
            progress_repository=lambda s: ProgressRepository(s),
        )
        app.dependency_overrides[get_container] = lambda: container

        async def override_session():
            yield session

        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_file_store] = lambda: store
        app.dependency_overrides[get_job_repository] = lambda: JobRepository(session)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/jobs")).status_code == 401
            short = await client.post(
                "/api/v1/auth/signup", json={"username": "badaccount", "password": "LEAK-ME"}
            )
            assert short.status_code == 422 and "LEAK-ME" not in short.text
            signup = await client.post(
                "/api/v1/auth/signup",
                json={"username": "Alice", "password": "alice-long-passphrase"},
            )
            assert signup.status_code == 201
            assert signup.json()["status"] == "PENDING"
            assert (
                await client.post(
                    "/api/v1/auth/login",
                    json={"username": "alice", "password": "alice-long-passphrase"},
                )
            ).status_code == 403
            client.cookies.set(COOKIE, "admin-test-token", path="/api/v1")
            users = (await client.get("/api/v1/admin/users")).json()
            alice_id = users[0]["id"]
            assert users[0]["status"] == "PENDING"
            assert (
                await client.put(f"/api/v1/admin/users/{alice_id}", json={"action": "approve"})
            ).status_code == 403  # CSRF
            approved = await client.put(
                f"/api/v1/admin/users/{alice_id}",
                json={"action": "approve"},
                headers={"X-CSRF-Token": "admin-csrf"},
            )
            assert approved.json()["status"] == "ACTIVE"
            client.cookies.clear()
            login = await client.post(
                "/api/v1/auth/login",
                json={"username": "alice", "password": "alice-long-passphrase"},
            )
            assert login.status_code == 200
            csrf = login.json()["csrf_token"]
            assert (await client.get("/api/v1/auth/session")).json()["username"] == "alice"
            # Jobs are filtered in SQL before pagination and detail/file routes return 404.
            session.add_all(
                [
                    Job(
                        id="owned",
                        owner_id=alice_id,
                        filename="mine.pdf",
                        stored_path="unused",
                        status=JobStatus.SUCCEEDED,
                    ),
                    Job(
                        id="private",
                        owner_id=1,
                        filename="legacy.pdf",
                        stored_path="unused",
                        status=JobStatus.SUCCEEDED,
                    ),
                ]
            )
            await session.commit()
            await store.save_output_bytes("private", "assets/figure.png", b"private")
            response = await client.get("/api/v1/jobs?limit=1")
            assert [job["id"] for job in response.json()] == ["owned"]
            for suffix in (
                "",
                "/chapters",
                "/chapters/1/preview",
                "/reviews",
                "/reviews/anything",
                "/assets/figure.png",
                "/download",
                "/events",
            ):
                path = f"/api/v1/jobs/private{suffix}"
                response = (
                    await client.put(
                        path, json={"decision": "ACCEPTED_IMAGE"}, headers={"X-CSRF-Token": csrf}
                    )
                    if suffix.startswith("/reviews/")
                    else await client.get(path)
                )
                assert response.status_code == 404, (path, response.text)
            assert (
                await client.delete("/api/v1/jobs/private", headers={"X-CSRF-Token": csrf})
            ).status_code == 404
            # Administrator sees both and can disable the user, revoking live sessions.
            client.cookies.set(COOKIE, "admin-test-token", path="/api/v1")
            assert len((await client.get("/api/v1/jobs")).json()) == 2
            code = (
                await client.post(
                    f"/api/v1/admin/users/{alice_id}/reset-code",
                    headers={"X-CSRF-Token": "admin-csrf"},
                )
            ).json()["code"]
            client.cookies.clear()
            reset_payload = {"username": "alice", "code": code, "password": "new-passphrase-long"}
            assert (
                await client.post(
                    "/api/v1/auth/reset-password", json={**reset_payload, "code": "wrong"}
                )
            ).status_code == 401
            reset_row = await session.get(PasswordResetCode, alice_id)
            assert reset_row is not None
            reset_row.expires_at = utcnow() - timedelta(seconds=1)
            await session.commit()
            assert (
                await client.post("/api/v1/auth/reset-password", json=reset_payload)
            ).status_code == 401
            client.cookies.set(COOKIE, "admin-test-token", path="/api/v1")
            code = (
                await client.post(
                    f"/api/v1/admin/users/{alice_id}/reset-code",
                    headers={"X-CSRF-Token": "admin-csrf"},
                )
            ).json()["code"]
            client.cookies.clear()
            reset_payload["code"] = code
            assert (
                await client.post("/api/v1/auth/reset-password", json=reset_payload)
            ).status_code == 200
            assert (
                await client.post("/api/v1/auth/reset-password", json=reset_payload)
            ).status_code == 401
            assert (await client.get("/api/v1/jobs")).status_code == 401
            assert (
                await client.post(
                    "/api/v1/auth/login",
                    json={"username": "alice", "password": "new-passphrase-long"},
                )
            ).status_code == 200
            client.cookies.set(COOKIE, "admin-test-token", path="/api/v1")
            await client.put(
                f"/api/v1/admin/users/{alice_id}",
                json={"action": "disable"},
                headers={"X-CSRF-Token": "admin-csrf"},
            )
            client.cookies.clear()
            assert (await client.get("/api/v1/jobs")).status_code == 401
            assert (
                await client.post(
                    "/api/v1/auth/login",
                    json={"username": "alice", "password": "new-passphrase-long"},
                )
            ).status_code == 403
    await redis.aclose()
    await engine.dispose()


async def test_email_shaped_sign_in_request_is_pending_and_visible_to_admin() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = fakeredis.aioredis.FakeRedis()
    async with factory() as session:
        session.add(
            User(
                id=1,
                username="admin",
                role="ADMIN",
                status="ACTIVE",
                password_hash=passwords.hash("admin-passphrase-long"),
            )
        )
        session.add(
            UserSession(
                user_id=1,
                token_hash=digest("admin-test-token"),
                csrf_token="admin-csrf",
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        await session.commit()
        app = create_app()
        container = SimpleNamespace(redis=redis, settings=Settings())
        app.dependency_overrides[get_container] = lambda: container

        async def override_session():
            yield session

        app.dependency_overrides[get_session] = override_session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/signup",
                json={
                    "username": "Reader.One+notes@Example.com",
                    "password": "a-long-password",
                },
            )
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "PENDING"
            assert (
                await client.post(
                    "/api/v1/auth/login",
                    json={
                        "username": "reader.one+notes@example.com",
                        "password": "a-long-password",
                    },
                )
            ).status_code == 403
            client.cookies.set(COOKIE, "admin-test-token", path="/api/v1")
            users = (await client.get("/api/v1/admin/users")).json()
            assert [(user["username"], user["status"]) for user in users] == [
                ("reader.one+notes@example.com", "PENDING")
            ]
            response = await client.put(
                f"/api/v1/admin/users/{users[0]['id']}",
                json={"action": "approve"},
                headers={"X-CSRF-Token": "admin-csrf"},
            )
            assert response.status_code == 200, response.text
            client.cookies.clear()
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "username": "Reader.One+notes@Example.com",
                    "password": "a-long-password",
                },
            )
            assert response.status_code == 200, response.text
            assert (await client.get("/api/v1/auth/session")).json()[
                "username"
            ] == "reader.one+notes@example.com"
    await redis.aclose()
    await engine.dispose()
