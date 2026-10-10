"""Admin authentication, CSRF, encryption and dynamic settings using SQLite."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.vlm.factory import build_vlm_provider
from app.api.deps import get_container, get_session
from app.api.v1.routers.admin import digest
from app.config import Settings
from app.db.models import Base, BootstrapCode, RuntimeSetting, User, utcnow
from app.main import create_app
from app.services.runtime_settings import RuntimeSettingsService


@pytest.mark.asyncio
async def test_setup_signin_and_runtime_settings(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            User(id=1, username="admin", password_hash="", role="ADMIN", status="SETUP_REQUIRED")
        )
        session.add(
            BootstrapCode(
                id=1, token_hash=digest("setup-code"), expires_at=utcnow() + timedelta(minutes=30)
            )
        )
        await session.commit()
        settings = Settings(settings_key_file=str(tmp_path / "secret.key"))
        container = SimpleNamespace(settings=settings, redis=fakeredis.aioredis.FakeRedis())
        app = create_app()
        app.dependency_overrides[get_container] = lambda: container

        async def override_session():
            yield session

        app.dependency_overrides[get_session] = override_session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/admin/session")).json()["setup_required"] is True
            assert (await client.get("/api/v1/admin/settings")).status_code == 401
            assert (
                await client.post(
                    "/api/v1/admin/setup", json={"code": "wrong", "password": "valid-long-password"}
                )
            ).status_code == 401
            invalid = await client.post(
                "/api/v1/admin/setup", json={"code": "setup-code", "password": "leak-me"}
            )
            assert invalid.status_code == 422 and "leak-me" not in invalid.text
            created = await client.post(
                "/api/v1/admin/setup",
                json={"code": "setup-code", "password": "valid-long-password"},
            )
            assert created.status_code == 200
            csrf = created.json()["csrf_token"]
            assert (
                await client.put("/api/v1/admin/settings", json={"vlm_provider": "cloud"})
            ).status_code == 403
            payload = {
                "vlm_provider": "openrouter",
                "openrouter_api_key": "private-secret",
                "openrouter_vision_model": "openai/gpt-4o-mini",
                "max_pages": 500,
            }
            saved = await client.put(
                "/api/v1/admin/settings", json=payload, headers={"X-CSRF-Token": csrf}
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["keys_configured"]["openrouter_api_key"] is True
            assert "private-secret" not in saved.text
            assert settings.openrouter_api_key == ""
            row = await session.get(RuntimeSetting, "openrouter_api_key")
            assert row is not None and row.value != "private-secret"
            resolved = await RuntimeSettingsService(session, settings).resolve()
            assert resolved.openrouter_api_key == "private-secret"
            assert resolved.vlm_provider == "openrouter"
            assert resolved.max_pages == 500
            assert build_vlm_provider(resolved)._providers[0][0] == "openrouter"  # type: ignore[attr-defined]
            assert (
                await client.put(
                    "/api/v1/admin/settings",
                    json={"max_upload_bytes": 100_000_000},
                    headers={"X-CSRF-Token": csrf},
                )
            ).status_code == 400
            assert (
                await client.post(
                    "/api/v1/admin/settings/check",
                    json={"provider": "openrouter"},
                    headers={"X-CSRF-Token": csrf},
                )
            ).status_code == 400
            clear = await client.put(
                "/api/v1/admin/settings",
                json={"openrouter_api_key": None},
                headers={"X-CSRF-Token": csrf},
            )
            assert clear.json()["keys_configured"]["openrouter_api_key"] is False
            await client.post("/api/v1/admin/logout", headers={"X-CSRF-Token": csrf})
            assert (await client.get("/api/v1/admin/settings")).status_code == 401
            assert (
                await client.post("/api/v1/admin/login", json={"password": "wrong"})
            ).status_code == 401
            login = await client.post(
                "/api/v1/admin/login", json={"password": "valid-long-password"}
            )
            assert login.status_code == 200
            assert (await client.get("/api/v1/admin/settings")).status_code == 200
    # A separate worker session sees the saved configuration without an API restart.
    async with factory() as worker_session:
        worker_settings = await RuntimeSettingsService(worker_session, settings).resolve()
        assert worker_settings.vlm_provider == "openrouter"
        assert worker_settings.max_pages == 500
        assert worker_settings.openrouter_api_key == ""
        # The key was explicitly cleared; the cloud provider chain is now empty.
        provider = build_vlm_provider(worker_settings)
        assert not provider._providers  # type: ignore[attr-defined]
    await engine.dispose()
