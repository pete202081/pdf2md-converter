"""Source-page hints and revision-safe chapter corrections.

Revision ID: 5d3c7f4102e9
Revises: c49f2a10b36d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d3c7f4102e9"
down_revision: str | Sequence[str] | None = "c49f2a10b36d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "quality_inventory_ready", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_table(
        "page_quality_flags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chapter_order", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("message", sa.String(200), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("job_id", "page_number", "code"),
    )
    op.create_index("ix_page_quality_flags_job_id", "page_quality_flags", ["job_id"])
    op.create_table(
        "chapter_edits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chapter_order", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("old_md_path", sa.Text(), nullable=False),
        sa.Column("old_hash", sa.String(64), nullable=False),
        sa.Column("new_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.Column("undone_by_id", sa.Integer(), sa.ForeignKey("users.id")),
    )
    op.create_index("ix_chapter_edits_job_id", "chapter_edits", ["job_id"])


def downgrade() -> None:
    raise RuntimeError("Restore the pre-Phase-6 database backup to remove quality review data")
