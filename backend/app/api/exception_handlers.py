"""Global exception handlers.

Map the domain exception hierarchy to a consistent JSON error envelope:
``{"error_code", "message", "correlation_id", "details"}``.

- :class:`AppError` subclasses → their own HTTP status, safe message.
- Anything unexpected → 500 with a generic message; the full traceback only
  goes to the logs. Internal details never leak to clients.
"""

from fastapi import Request
from fastapi.responses import JSONResponse

from app.domain.exceptions import AppError
from app.logging import get_logger

logger = get_logger("api.errors")


def _correlation_id(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle expected application errors."""
    logger.warning(
        "app_error",
        error_code=exc.error_code,
        message=exc.message,
        path=str(request.url.path),
        correlation_id=_correlation_id(request),
    )
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "correlation_id": _correlation_id(request),
            "details": exc.details,
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected errors without leaking internals."""
    logger.exception(
        "unhandled_error",
        path=str(request.url.path),
        correlation_id=_correlation_id(request),
    )
    return JSONResponse(
        status_code=500,
        content={
            "error_code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
            "correlation_id": _correlation_id(request),
            "details": {},
        },
    )
