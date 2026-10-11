"""Cooperative cancellation request for queued/running conversion jobs.

Revision ID: c97a34d109ff
Revises: b36271ed90ac
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c97a34d109ff"
down_revision: str | Sequence[str] | None = "b36271ed90ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("cancel_requested_at")
