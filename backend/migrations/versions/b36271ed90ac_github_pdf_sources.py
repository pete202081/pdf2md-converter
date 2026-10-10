"""Private provenance for GitHub-sourced conversion jobs.

Revision ID: b36271ed90ac
Revises: 97d3e8c410ab
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b36271ed90ac"
down_revision: str | Sequence[str] | None = "97d3e8c410ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column("source_type", sa.String(16), nullable=False, server_default="UPLOAD")
        )
        batch.add_column(sa.Column("source_repo", sa.String(256), nullable=True))
        batch.add_column(sa.Column("source_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("source_revision", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("source_revision")
        batch.drop_column("source_path")
        batch.drop_column("source_repo")
        batch.drop_column("source_type")
