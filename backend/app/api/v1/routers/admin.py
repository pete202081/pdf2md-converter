"""Single-administrator sessions and runtime settings. No key is sent to a browser."""

import hashlib
import hmac
import io
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.vlm.http_providers import (
    AnthropicVisionProvider,
    OpenAiVisionProvider,
    OpenRouterVisionProvider,
)
from app.api.deps import get_container, get_session
from app.config import Settings
from app.container import Container
from app.db.models import AdminAccount, AdminSession, BootstrapCode, utcnow
from app.domain.exceptions import ProviderError
from app.services.runtime_settings import RuntimeSettingsService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
passwords = PasswordHasher()
COOKIE = "pdf2md_admin"


def expired(value: datetime) -> bool:
    return value.replace(tzinfo=UTC) <= utcnow() if value.tzinfo is None else value <= utcnow()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vlm_provider: Literal["hybrid", "cloud", "local", "openrouter"] | None = None
    ollama_base_url: str | None = None
    ollama_vision_model: str | None = None
    openrouter_vision_model: str | None = None
    openrouter_site_url: str | None = None
    openrouter_app_name: str | None = None
    openai_vision_model: str | None = None
    anthropic_vision_model: str | None = None
    vlm_timeout_seconds: float | None = None
    max_pages: int | None = None
    max_upload_bytes: int | None = None
    # Absent means keep unchanged; null or empty explicitly clears the key.
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None


async def _throttle(request: Request, container: Container) -> None:
    # Redis is shared by replicas; 6 failures/15 min per IP. Also cap attempts
    # even for nonexistent accounts to avoid an account-enumeration side channel.
    address = request.headers.get("x-real-ip") or (
        request.client.host if request.client else "unknown"
    )
    key = f"pdf2md:admin:attempts:{address}"
    count = await container.redis.incr(key)
    if count == 1:
        await container.redis.expire(key, 900)
    if count > 6:
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")


def _cookie(response: Response, request: Request, token: str) -> None:
    # Remove sessions issued by older builds (scoped only to /api/v1/admin).
    response.delete_cookie(COOKIE, path="/api/v1/admin")
    response.set_cookie(
        COOKIE,
        token,
        max_age=12 * 3600,
        httponly=True,
        samesite="strict",
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
        path="/api/v1",
    )


async def _new_session(session: AsyncSession) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session.add(
        AdminSession(
            token_hash=digest(token), csrf_token=csrf, expires_at=utcnow() + timedelta(hours=12)
        )
    )
    await session.commit()
    return token, csrf


async def _current(request: Request, session: AsyncSession) -> AdminSession | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    found = await session.get(AdminSession, digest(token))
    return found if found and not expired(found.expires_at) else None


async def require_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AdminSession:
    found = await _current(request, session)
    if not found:
        raise HTTPException(401, "Administrator login required")
    if request.method not in ("GET", "HEAD") and not hmac.compare_digest(
        request.headers.get("x-csrf-token", ""), found.csrf_token
    ):
        raise HTTPException(403, "Invalid CSRF token")
    return found


@router.get("/session")
async def session_status(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    admin = await session.get(AdminAccount, 1)
    current = await _current(request, session)
    if current and request.cookies.get(COOKIE):
        _cookie(response, request, request.cookies[COOKIE])
    return {
        "setup_required": admin is None,
        "authenticated": current is not None,
        "csrf_token": current.csrf_token if current else None,
    }


@router.post("/setup")
async def setup(
    body: SetupRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    await _throttle(request, container)
    if await session.get(AdminAccount, 1):
        raise HTTPException(409, "Administrator already exists")
    code = await session.get(BootstrapCode, 1)
    if (
        not code
        or expired(code.expires_at)
        or not hmac.compare_digest(digest(body.code), code.token_hash)
    ):
        raise HTTPException(401, "Invalid or expired setup code")
    session.add(AdminAccount(id=1, password_hash=passwords.hash(body.password)))
    await session.delete(code)
    # In a multi-API deployment, a concurrent setup must not create a second admin.
    await session.commit()
    token, csrf = await _new_session(session)
    _cookie(response, request, token)
    return {"authenticated": True, "csrf_token": csrf}


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    await _throttle(request, container)
    admin = await session.get(AdminAccount, 1)
    try:
        if not admin or not passwords.verify(admin.password_hash, body.password):
            raise HTTPException(401, "Invalid credentials")
    except VerifyMismatchError as exc:
        raise HTTPException(401, "Invalid credentials") from exc
    token, csrf = await _new_session(session)
    _cookie(response, request, token)
    address = request.headers.get("x-real-ip") or (
        request.client.host if request.client else "unknown"
    )
    await container.redis.delete(f"pdf2md:admin:attempts:{address}")
    return {"authenticated": True, "csrf_token": csrf}


@router.post("/logout")
async def logout(
    response: Response,
    admin: AdminSession = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    await session.delete(admin)
    await session.commit()
    response.delete_cookie(COOKIE, path="/api/v1")
    response.delete_cookie(COOKIE, path="/api/v1/admin")
    return {"authenticated": False}


@router.get("/settings", dependencies=[Depends(require_admin)])
async def get_settings_view(
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    return await RuntimeSettingsService(session, container.settings).public()


@router.put("/settings", dependencies=[Depends(require_admin)])
async def update_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    provided = body.model_dump(exclude_unset=True)
    keys: dict[str, str | None] = {
        k: provided.pop(k)
        for k in ("openrouter_api_key", "openai_api_key", "anthropic_api_key")
        if k in provided
    }
    if any(value is None for value in provided.values()):
        raise HTTPException(422, "Non-secret settings cannot be null")
    return await RuntimeSettingsService(session, container.settings).save(provided, keys)


class ProviderCheck(BaseModel):
    provider: Literal["ollama", "openrouter", "openai", "anthropic"]
    acknowledge_cloud_cost: bool = False


@router.post("/settings/check", dependencies=[Depends(require_admin)])
async def check_provider(
    body: ProviderCheck,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    """Ollama tags are free; cloud checks explicitly require consent and can be billed."""
    settings: Settings = await RuntimeSettingsService(session, container.settings).resolve()
    if body.provider != "ollama":
        if not body.acknowledge_cloud_cost:
            raise HTTPException(400, "Confirm potential cloud-provider charges before testing")
        options = {
            "openrouter": (
                settings.openrouter_api_key,
                OpenRouterVisionProvider(
                    settings.openrouter_api_key,
                    settings.openrouter_vision_model,
                    timeout=settings.vlm_timeout_seconds,
                    site_url=settings.openrouter_site_url,
                    app_name=settings.openrouter_app_name,
                ),
            ),
            "openai": (
                settings.openai_api_key,
                OpenAiVisionProvider(
                    settings.openai_api_key,
                    settings.openai_vision_model,
                    timeout=settings.vlm_timeout_seconds,
                ),
            ),
            "anthropic": (
                settings.anthropic_api_key,
                AnthropicVisionProvider(
                    settings.anthropic_api_key,
                    settings.anthropic_vision_model,
                    timeout=settings.vlm_timeout_seconds,
                ),
            ),
        }
        key, provider = options[body.provider]
        if not key:
            raise HTTPException(400, "Provider key is not configured")
        image = Image.new("RGB", (160, 70), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((5, 10, 45, 40), outline="black", width=2)
        draw.rectangle((110, 10, 150, 40), outline="black", width=2)
        draw.line((45, 25, 110, 25), fill="black", width=2)
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")
        try:
            await provider.diagram_to_mermaid(image_bytes.getvalue(), "image/png")
            return {"reachable": True, "model_available": True}
        except ProviderError:
            return {
                "reachable": False,
                "model_available": False,
                "message": "Provider request failed. Check its key, model and account access.",
            }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            result = await client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
            result.raise_for_status()
            models = [m.get("name", "") for m in result.json().get("models", [])]
        return {
            "reachable": True,
            "model_available": any(
                name == settings.ollama_vision_model
                or name.startswith(f"{settings.ollama_vision_model}:")
                for name in models
            ),
        }
    except (httpx.HTTPError, ValueError, KeyError):
        return {"reachable": False, "model_available": False}
