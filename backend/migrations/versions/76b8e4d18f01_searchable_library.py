"""Private chapter full-text search.

Revision ID: 76b8e4d18f01
Revises: 5d3c7f4102e9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "76b8e4d18f01"
down_revision: str | Sequence[str] | None = "5d3c7f4102e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("search_index_state", sa.String(16), nullable=False, server_default="PENDING"),
    )
    op.create_table(
        "search_documents",
        sa.Column(
            "job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("chapter_order", sa.Integer(), primary_key=True),
        sa.Column("chapter_title", sa.String(512), nullable=False),
        sa.Column("book_filename", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("first_page", sa.Integer()),
        sa.Column("last_page", sa.Integer()),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_search_documents_fts ON search_documents "
            "USING gin (to_tsvector('english'::regconfig, content))"
        )


def downgrade() -> None:
    raise RuntimeError("Restore the pre-Phase-7 database backup to remove search data")
