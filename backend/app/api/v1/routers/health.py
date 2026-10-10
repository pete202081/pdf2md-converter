"""Health and readiness probes (used by Docker healthchecks)."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import get_container
from app.config import get_settings
from app.container import Container

router = APIRouter(tags=["health"])


@router.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    """Service info."""
    settings = get_settings()
    return {
        "name": settings.app_name,
        "version": "0.1.0",
        "docs": "/docs",
        "api": "/api/v1/jobs",
    }


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe: the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(container: Container = Depends(get_container)) -> JSONResponse:
    """Readiness probe: dependencies (DB, Redis) are reachable."""
    checks: dict[str, str] = {}
    try:
        async with container.database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    try:
        await container.redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
    )
