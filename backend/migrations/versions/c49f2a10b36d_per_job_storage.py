"""Record backend per job; all preexisting jobs stay local.

Revision ID: c49f2a10b36d
Revises: 884c9c455a88
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c49f2a10b36d"
down_revision: str | Sequence[str] | None = "884c9c455a88"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("storage_backend", sa.String(16), nullable=False, server_default="local")
    )


def downgrade() -> None:
    raise RuntimeError("Restoring a backup is required to change job storage metadata")
