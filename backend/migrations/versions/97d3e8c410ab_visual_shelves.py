"""Immutable bookshelves and per-reader progress.

Revision ID: 97d3e8c410ab
Revises: 76b8e4d18f01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "97d3e8c410ab"
down_revision: str | Sequence[str] | None = "76b8e4d18f01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shelf_books",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("author", sa.String(120)),
        sa.Column("description", sa.String(1000)),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("visibility", sa.String(8), nullable=False),
        sa.Column("storage_backend", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shelf_books_owner_id", "shelf_books", ["owner_id"])
    op.create_index("ix_shelf_books_visibility", "shelf_books", ["visibility"])
    op.create_table(
        "shelf_chapters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "book_id",
            sa.String(36),
            sa.ForeignKey("shelf_books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("relative_path", sa.String(128), nullable=False),
        sa.UniqueConstraint("book_id", "order_index"),
    )
    op.create_index("ix_shelf_chapters_book_id", "shelf_chapters", ["book_id"])
    op.create_table(
        "shelf_assets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "book_id",
            sa.String(36),
            sa.ForeignKey("shelf_books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("media_type", sa.String(40), nullable=False),
        sa.UniqueConstraint("book_id", "name"),
    )
    op.create_index("ix_shelf_assets_book_id", "shelf_assets", ["book_id"])
    op.create_table(
        "reading_positions",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "book_id",
            sa.String(36),
            sa.ForeignKey("shelf_books.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "chapter_id",
            sa.Integer(),
            sa.ForeignKey("shelf_chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "chapter_completions",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "chapter_id",
            sa.Integer(),
            sa.ForeignKey("shelf_chapters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Restore the pre-Phase-8 backup to remove shelf data")
