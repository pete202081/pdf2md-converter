"""FastAPI application factory and entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.api.exception_handlers import (
    app_error_handler,
    unhandled_exception_handler,
)
from app.api.v1.routers import admin, auth, events, github, health, jobs, metrics, search, shelves
from app.config import get_settings
from app.container import build_container
from app.domain.exceptions import AppError
from app.logging import configure_logging
from app.middleware.correlation import CorrelationIdMiddleware
from app.middleware.metrics import MetricsMiddleware


async def safe_validation_error(request: Request, exc: RequestValidationError) -> Response:
    # FastAPI's default 422 response echoes invalid inputs, including submitted keys.
    if request.url.path.startswith(
        ("/api/v1/admin", "/api/v1/auth", "/api/v1/search", "/api/v1/shelves", "/api/v1/github")
    ):
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "INVALID_REQUEST",
                "message": "Invalid account or administrator request",
                "fields": [".".join(map(str, error["loc"])) for error in exc.errors()],
            },
        )
    return await request_validation_exception_handler(request, exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown: configure logging and wire the container; migrations run separately."""
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)
    app.state.container = container
    yield
    await container.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(MetricsMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(events.router)
    app.include_router(admin.router)
    app.include_router(auth.router)
    app.include_router(metrics.router)
    app.include_router(search.router)
    app.include_router(shelves.router)
    app.include_router(github.router)

    app.add_exception_handler(RequestValidationError, safe_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    return app


app = create_app()
