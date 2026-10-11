"""Administrator setup, account approvals and settings; secrets stay server-side."""

import io
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.github_pdf import GitHubPDF
from app.adapters.vlm.http_providers import (
    AnthropicVisionProvider,
    OpenAiVisionProvider,
    OpenRouterVisionProvider,
)
from app.api.deps import get_container, get_session
from app.api.v1.routers.auth import (
    current_session,
    digest,
    expired,
    new_session,
    passwords,
    require_admin,
    set_cookie,
    throttle,
)
from app.api.v1.routers.auth import (
    login as user_login,
)
from app.api.v1.routers.github import get_github
from app.config import Settings
from app.container import Container
from app.db.models import BootstrapCode, User, UserSession
from app.domain.exceptions import ProviderError
from app.services.rate_limits import RateLimiter
from app.services.runtime_settings import RuntimeSettingsService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


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
    github_pat: str | None = None


@router.get("/session")
async def session_status(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    admin = await session.get(User, 1)
    current = await current_session(request, session)
    return {
        "setup_required": admin is None or admin.status == "SETUP_REQUIRED",
        "authenticated": current is not None and current[0].role == "ADMIN",
        "csrf_token": current[1].csrf_token if current and current[0].role == "ADMIN" else None,
    }


@router.post("/setup")
async def setup(
    body: SetupRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    await throttle(request, container, "setup", "admin")
    admin = await session.get(User, 1)
    if not admin or admin.status != "SETUP_REQUIRED":
        raise HTTPException(409, "Administrator already exists")
    code = await session.get(BootstrapCode, 1)
    if not code or expired(code.expires_at) or digest(body.code) != code.token_hash:
        raise HTTPException(401, "Invalid or expired setup code")
    # Compare-and-swap: only one caller may complete setup.
    from sqlalchemy import update

    result = await session.execute(
        update(User)
        .where(User.id == 1, User.status == "SETUP_REQUIRED")
        .values(password_hash=passwords.hash(body.password), status="ACTIVE")
        .returning(User.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(409, "Administrator already configured")
    await session.delete(code)
    await session.commit()
    token, csrf = await new_session(session, admin)
    set_cookie(response, request, token)
    return {"authenticated": True, "csrf_token": csrf}


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    # Preserve the original admin-only login endpoint for first-time setup UI.
    from app.api.v1.routers.auth import LoginRequest as UserLoginRequest

    result = await user_login(
        UserLoginRequest(username="admin", password=body.password),
        request,
        response,
        session,
        container,
    )
    return result


@router.post("/logout")
async def logout(
    response: Response,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    from app.api.v1.routers.auth import logout as user_logout

    return await user_logout(response, request, admin, session)


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
        for k in ("openrouter_api_key", "openai_api_key", "anthropic_api_key", "github_pat")
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
    request: Request,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
    admin: User = Depends(require_admin),
) -> dict[str, object]:
    """Ollama tags are free; cloud checks explicitly require consent and can be billed."""
    await RateLimiter(container.redis, container.settings).costly(request, "provider", admin.id)
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


class AccountAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject", "disable", "enable"]


@router.get("/users")
async def list_accounts(
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[dict[str, object]]:
    from sqlalchemy import select

    users = (
        await session.execute(select(User).where(User.role == "USER").order_by(User.id))
    ).scalars()
    return [
        {
            "id": user.id,
            "username": user.username,
            "status": user.status,
            "created_at": user.created_at.isoformat(),
        }
        for user in users
    ]


@router.put("/users/{user_id}")
async def manage_account(
    user_id: int,
    body: AccountAction,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    from sqlalchemy import delete

    user = await session.get(User, user_id)
    if not user or user.role == "ADMIN":
        raise HTTPException(404, "Account not found")
    allowed = {
        "approve": ("PENDING", "ACTIVE"),
        "reject": ("PENDING", "REJECTED"),
        "disable": ("ACTIVE", "DISABLED"),
        "enable": ("DISABLED", "ACTIVE"),
    }
    before, after = allowed[body.action]
    if user.status != before:
        raise HTTPException(409, f"Only {before.lower()} accounts can be {body.action}d")
    user.status = after
    await session.execute(delete(UserSession).where(UserSession.user_id == user_id))
    await session.commit()
    return {"status": after}


@router.post("/users/{user_id}/reset-code")
async def issue_reset_code(
    user_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    import secrets
    from datetime import timedelta

    from app.db.models import PasswordResetCode, utcnow

    user = await session.get(User, user_id)
    if not user or user.role != "USER" or user.status != "ACTIVE":
        raise HTTPException(404, "Approved account not found")
    code = secrets.token_urlsafe(24)
    await session.merge(
        PasswordResetCode(
            user_id=user_id, token_hash=digest(code), expires_at=utcnow() + timedelta(minutes=15)
        )
    )
    await session.commit()
    # Display once to the admin to share via a trusted channel; never log or email it.
    return {"code": code, "expires_in": "15 minutes"}


@router.post("/settings/github/check")
async def check_github_token(
    request: Request,
    admin: User = Depends(require_admin),
    container: Container = Depends(get_container),
    github: GitHubPDF = Depends(get_github),
) -> dict[str, bool]:
    await RateLimiter(container.redis, container.settings).costly(request, "provider", admin.id)
    return {"reachable": await github.check()}
