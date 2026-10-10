"""Tests for the domain exception hierarchy."""

from app.domain.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    ProcessingError,
    ProviderError,
    StageError,
    StorageError,
    ValidationError,
)


def test_hierarchy() -> None:
    assert issubclass(ValidationError, AppError)
    assert issubclass(NotFoundError, AppError)
    assert issubclass(ConflictError, AppError)
    assert issubclass(StorageError, AppError)
    assert issubclass(StageError, ProcessingError)
    assert issubclass(ProviderError, ProcessingError)
    assert issubclass(ProcessingError, AppError)


def test_http_status_mapping() -> None:
    assert ValidationError.http_status == 400
    assert NotFoundError.http_status == 404
    assert ConflictError.http_status == 409
    assert ProcessingError.http_status == 500
    assert StorageError.http_status == 500
    assert AppError.http_status == 500


def test_error_codes_are_unique_and_stable() -> None:
    codes = {
        ValidationError.error_code,
        NotFoundError.error_code,
        ConflictError.error_code,
        StorageError.error_code,
        ProcessingError.error_code,
        StageError.error_code,
        ProviderError.error_code,
    }
    assert len(codes) == 7


def test_message_and_details() -> None:
    error = ValidationError("bad file", details={"filename": "x.txt"})
    assert error.message == "bad file"
    assert error.details == {"filename": "x.txt"}
    assert str(error) == "bad file"

    default_details = ValidationError("no details")
    assert default_details.details == {}
