"""Pydantic schemas for API request/response bodies."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.enums import JobStatus, StageStatus


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage_name: str
    status: StageStatus
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    metrics: dict[str, Any]


class ChapterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_index: int
    title: str
    folder_name: str
    md_path: str | None
    mermaid_count: int
    image_count: int
    word_count: int


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    source_type: str = "UPLOAD"
    source_repo: str | None = None
    source_path: str | None = None
    source_revision: str | None = None
    status: JobStatus
    page_count: int
    chapter_count: int
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    cancel_requested_at: datetime | None = None
    review_total: int = 0
    review_pending: int = 0
    search_index_state: str = "PENDING"


class JobDetailOut(JobOut):
    stages: list[StageOut]
    chapters: list[ChapterOut]


class ErrorOut(BaseModel):
    """Consistent error envelope returned by the exception handlers."""

    error_code: str
    message: str
    correlation_id: str | None = None
    details: dict[str, Any] = {}
