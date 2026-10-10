"""Exception hierarchy.

Every expected failure in the system is an :class:`AppError` subclass carrying a
machine-readable ``error_code``, an HTTP status and a *safe* user-facing
message. The API layer maps these to a consistent JSON error envelope, so
internal details never leak to clients.
"""

from typing import Any


class AppError(Exception):
    """Base class for all expected application errors."""

    error_code: str = "APP_ERROR"
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}


class ValidationError(AppError):
    """The client sent something invalid (bad file, bad request)."""

    error_code = "VALIDATION_ERROR"
    http_status = 400


class NotFoundError(AppError):
    """The requested resource does not exist."""

    error_code = "NOT_FOUND"
    http_status = 404


class ConflictError(AppError):
    """The request conflicts with the current state of the resource."""

    error_code = "CONFLICT"
    http_status = 409


class StorageError(AppError):
    """Reading/writing files or object storage failed."""

    error_code = "STORAGE_ERROR"
    http_status = 500


class ProcessingError(AppError):
    """A processing step failed."""

    error_code = "PROCESSING_ERROR"
    http_status = 500


class StageError(ProcessingError):
    """A pipeline stage failed."""

    error_code = "STAGE_ERROR"


class ProviderError(ProcessingError):
    """An external provider (e.g. a VLM API) failed."""

    error_code = "PROVIDER_ERROR"
