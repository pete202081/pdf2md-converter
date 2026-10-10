"""Self-registration, approved-user sessions and shared authorization guards."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session
from app.config import get_settings
from app.container import Container
from app.db.models import Job, User, UserSession, utcnow
from app.domain.exceptions import NotFoundError
from app.services.rate_limits import RateLimiter

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
passwords = PasswordHasher()
COOKIE = "pdf2md_session"
USERNAME = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$", re.ASCII)
# An email-shaped identifier is a username, NOT a verified contact address.
EMAIL_USERNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9._%+-]*[a-z0-9])?@"
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$",
    re.ASCII,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def expired(value: datetime) -> bool:
    return value.replace(tzinfo=UTC) <= utcnow() if value.tzinfo is None else value <= utcnow()


async def throttle(
    request: Request, container: Container, kind: str = "login", username: str = ""
) -> None:
    await RateLimiter(container.redis, container.settings).auth(request, kind, username)


def set_cookie(response: Response, request: Request, token: str) -> None:
    response.delete_cookie("pdf2md_admin", path="/api/v1")
    response.delete_cookie("pdf2md_admin", path="/api/v1/admin")
    response.set_cookie(
        COOKIE,
        token,
        max_age=12 * 3600,
        httponly=True,
        samesite="strict",
        secure=(
            get_settings().force_secure_cookies
            or request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        ),
        path="/api/v1",
    )


async def new_session(session: AsyncSession, user: User) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session.add(
        UserSession(
            token_hash=digest(token),
            user_id=user.id,
            csrf_token=csrf,
            expires_at=utcnow() + timedelta(hours=12),
        )
    )
    await session.commit()
    return token, csrf


async def current_session(
    request: Request, session: AsyncSession
) -> tuple[User, UserSession] | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    found = await session.get(UserSession, digest(token))
    if not found or expired(found.expires_at):
        return None
    user = await session.get(User, found.user_id)
    if not user or user.status != "ACTIVE":
        return None
    return user, found


async def require_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    current = await current_session(request, session)
    if current is None:
        raise HTTPException(401, "Sign in with an approved account")
    user, found = current
    if request.method not in ("GET", "HEAD") and not hmac.compare_digest(
        request.headers.get("x-csrf-token", ""), found.csrf_token
    ):
        raise HTTPException(403, "Invalid CSRF token")
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(403, "Administrator access required")
    return user


def check_owner(job: Job | None, user: User) -> Job:
    # Identical response for absent and inaccessible IDs, even to pending users.
    if job is None or (user.role != "ADMIN" and job.owner_id != user.id):
        raise NotFoundError("Job not found")
    return job


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/signup", status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, str]:
    await throttle(request, container, "signup", body.username)
    username = body.username.strip().lower()
    if username == "admin" or not (
        USERNAME.fullmatch(username) or EMAIL_USERNAME.fullmatch(username)
    ):
        raise HTTPException(422, "Use a 3–64 character username or email-shaped sign-in name")
    session.add(
        User(
            username=username,
            password_hash=passwords.hash(body.password),
            role="USER",
            status="PENDING",
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Username unavailable") from exc
    return {"status": "PENDING", "message": "Account request submitted for administrator approval"}


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    await throttle(request, container, "login", body.username)
    username = body.username.strip().lower()
    user = (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    try:
        if (
            not user
            or not user.password_hash
            or not passwords.verify(user.password_hash, body.password)
        ):
            raise HTTPException(401, "Invalid credentials or account not approved")
    except VerificationError as exc:
        raise HTTPException(401, "Invalid credentials or account not approved") from exc
    if user.status != "ACTIVE":
        raise HTTPException(403, "Account not approved or disabled")
    token, csrf = await new_session(session, user)
    set_cookie(response, request, token)
    return {"authenticated": True, "username": user.username, "role": user.role, "csrf_token": csrf}


@router.get("/session")
async def session_status(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    current = await current_session(request, session)
    return {
        "authenticated": current is not None,
        "username": current[0].username if current else None,
        "role": current[0].role if current else None,
        "csrf_token": current[1].csrf_token if current else None,
    }


@router.post("/logout")
async def logout(
    response: Response,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    token = request.cookies.get(COOKIE)
    if token:
        found = await session.get(UserSession, digest(token))
        if found and found.user_id == user.id:
            await session.delete(found)
            await session.commit()
    response.delete_cookie(COOKIE, path="/api/v1")
    return {"authenticated": False}


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    code: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=256)


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, str]:
    from sqlalchemy import delete

    from app.db.models import PasswordResetCode

    await throttle(request, container, "reset", body.username)
    user = (
        await session.execute(select(User).where(User.username == body.username.strip().lower()))
    ).scalar_one_or_none()
    reset = await session.get(PasswordResetCode, user.id) if user else None
    if (
        not user
        or user.status != "ACTIVE"
        or not reset
        or expired(reset.expires_at)
        or not hmac.compare_digest(digest(body.code), reset.token_hash)
    ):
        raise HTTPException(401, "Invalid or expired reset code")
    user.password_hash = passwords.hash(body.password)
    await session.delete(reset)
    await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await session.commit()
    return {"message": "Password changed; sign in again"}


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    from sqlalchemy import delete

    try:
        if not passwords.verify(user.password_hash, body.current_password):
            raise HTTPException(401, "Invalid current password")
    except VerificationError as exc:
        raise HTTPException(401, "Invalid current password") from exc
    user.password_hash = passwords.hash(body.new_password)
    await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await session.commit()
    return {"message": "Password changed; all sessions signed out"}
