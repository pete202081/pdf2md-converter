"""SQLAlchemy ORM models (SQLAlchemy 2.0 style)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import JobStatus, StageStatus


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Job(Base):
    """A single conversion job (one uploaded PDF)."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True, default=1
    )
    storage_backend: Mapped[str] = mapped_column(
        String(16), nullable=False, default="local", server_default="local"
    )
    filename: Mapped[str] = mapped_column(String(512))
    source_type: Mapped[str] = mapped_column(String(16), default="UPLOAD", server_default="UPLOAD")
    source_repo: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stored_path: Mapped[str] = mapped_column(Text)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, native_enum=False, length=16), default=JobStatus.QUEUED, index=True
    )
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_inventory_ready: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    search_index_state: Mapped[str] = mapped_column(
        String(16), default="PENDING", server_default="PENDING"
    )
    chapter_count: Mapped[int] = mapped_column(Integer, default=0)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    stages: Mapped[list[JobStage]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobStage.id"
    )
    chapters: Mapped[list[Chapter]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Chapter.order_index"
    )
    events: Mapped[list[ProgressEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    review_items: Mapped[list[DiagramReviewItem]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def review_total(self) -> int:
        return len(self.review_items)

    @property
    def review_pending(self) -> int:
        return sum(item.decision == "PENDING" for item in self.review_items)


class JobStage(Base):
    """Per-stage execution record for a job (audit + progress UI)."""

    __tablename__ = "job_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[StageStatus] = mapped_column(
        SAEnum(StageStatus, native_enum=False, length=16), default=StageStatus.PENDING
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    job: Mapped[Job] = relationship(back_populates="stages")


class Chapter(Base):
    """A generated chapter: one ordered folder + one Markdown file."""

    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(512))
    folder_name: Mapped[str] = mapped_column(String(512))
    md_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mermaid_count: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)

    job: Mapped[Job] = relationship(back_populates="chapters")


class ProgressEvent(Base):
    """A single progress update (persisted + fanned out over Redis pub/sub)."""

    __tablename__ = "progress_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    stage: Mapped[str] = mapped_column(String(128))
    percent: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")

    job: Mapped[Job] = relationship(back_populates="events")


class RuntimeSetting(Base):
    """Allowlisted runtime override; provider keys are Fernet-encrypted."""

    __tablename__ = "runtime_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class User(Base):
    """Admin or approved book owner; pending users cannot access jobs."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(12), default="USER")
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BootstrapCode(Base):
    __tablename__ = "bootstrap_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DiagramReviewItem(Base):
    """One failed diagram occurrence, not one unique underlying image asset."""

    __tablename__ = "diagram_review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    chapter_order: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer)  # 1-based, user-facing
    occurrence_index: Mapped[int] = mapped_column(Integer, default=0)
    position_y: Mapped[float] = mapped_column(Float, default=0.0)
    asset_name: Mapped[str] = mapped_column(String(512))
    reason: Mapped[str] = mapped_column(String(64), default="provider_unavailable")
    decision: Mapped[str] = mapped_column(String(24), default="PENDING")
    mermaid_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="review_items")


class PageQualityFlag(Base):
    """Auditable, rule-based warning for a source page of a NEW conversion."""

    __tablename__ = "page_quality_flags"
    __table_args__ = (UniqueConstraint("job_id", "page_number", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)  # 1-based
    chapter_order: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ChapterEdit(Base):
    """The prior chapter pointer supports safe undo without storing text in SQL."""

    __tablename__ = "chapter_edits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    chapter_order: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    old_md_path: Mapped[str] = mapped_column(Text)
    old_hash: Mapped[str] = mapped_column(String(64))
    new_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class SearchDocument(Base):
    """One current chapter revision, indexed for private book search."""

    __tablename__ = "search_documents"

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    chapter_order: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_title: Mapped[str] = mapped_column(String(512))
    book_filename: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    first_page: Mapped[int | None] = mapped_column(Integer)
    last_page: Mapped[int | None] = mapped_column(Integer)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShelfBook(Base):
    """A shelf-owned immutable reading copy, independent of its source job."""

    __tablename__ = "shelf_books"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    author: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(1000))
    source_kind: Mapped[str] = mapped_column(String(24))
    visibility: Mapped[str] = mapped_column(String(8), default="PRIVATE", index=True)
    storage_backend: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    chapters: Mapped[list[ShelfChapter]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="ShelfChapter.order_index",
        lazy="selectin",
    )
    assets: Mapped[list[ShelfAsset]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ShelfChapter(Base):
    __tablename__ = "shelf_chapters"
    __table_args__ = (UniqueConstraint("book_id", "order_index"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("shelf_books.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    relative_path: Mapped[str] = mapped_column(String(128))
    book: Mapped[ShelfBook] = relationship(back_populates="chapters")


class ShelfAsset(Base):
    __tablename__ = "shelf_assets"
    __table_args__ = (UniqueConstraint("book_id", "name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("shelf_books.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    media_type: Mapped[str] = mapped_column(String(40))
    book: Mapped[ShelfBook] = relationship(back_populates="assets")


class ReadingPosition(Base):
    __tablename__ = "reading_positions"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    book_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("shelf_books.id", ondelete="CASCADE"), primary_key=True
    )
    chapter_id: Mapped[int] = mapped_column(ForeignKey("shelf_chapters.id", ondelete="CASCADE"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChapterCompletion(Base):
    __tablename__ = "chapter_completions"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("shelf_chapters.id", ondelete="CASCADE"), primary_key=True
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
