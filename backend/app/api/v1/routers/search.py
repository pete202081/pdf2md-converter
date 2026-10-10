"""Authenticated, CSRF-protected private library lookup. No terms in URLs/logs."""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session
from app.api.v1.routers.auth import require_user
from app.container import Container
from app.db.models import User
from app.logging import get_logger
from app.services.library_search import search_library
from app.services.rate_limits import RateLimiter

router = APIRouter(prefix="/api/v1/search", tags=["library"])
log = get_logger("search_api")


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=100)
    limit: int = Field(default=20, ge=1, le=50)


@router.post("", response_model=None)
async def search(
    body: SearchRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, object] | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    query = body.query.strip()
    if len(query) < 2:
        return JSONResponse(
            status_code=422,
            headers={"Cache-Control": "private, no-store"},
            content={"error_code": "INVALID_REQUEST", "message": "Enter at least two characters"},
        )
    limiter = RateLimiter(container.redis, container.settings)
    await limiter.check("search:user", str(user.id), limit=60, window=3600)
    await limiter.check("search:ip", limiter.remote_ip(request), limit=240, window=3600)
    try:
        return await search_library(session, user.id, user.role == "ADMIN", query, body.limit)
    except Exception:
        # DB exception strings may contain bound private terms: never log an exception here.
        log.warning("search_unavailable", user_id=user.id)
        return JSONResponse(
            status_code=503,
            headers={"Cache-Control": "private, no-store"},
            content={
                "error_code": "SEARCH_UNAVAILABLE",
                "message": "Library search is temporarily unavailable",
            },
        )
