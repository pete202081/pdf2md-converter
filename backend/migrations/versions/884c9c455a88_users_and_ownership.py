"""Add users and private job ownership; preserve the existing admin and jobs.

Revision ID: 884c9c455a88
Revises: 40f38ec4bfb1
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "884c9c455a88"
down_revision: str | Sequence[str] | None = "40f38ec4bfb1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    legacy_hash = connection.execute(
        sa.text("SELECT password_hash FROM admin_accounts WHERE id = 1")
    ).scalar_one_or_none()
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    # An empty bootstrap-only database still needs an owner identity for the FK.
    # No default password is created; CLI setup changes SETUP_REQUIRED to ACTIVE.
    connection.execute(
        sa.text("""INSERT INTO users (id, username, password_hash, role, status, created_at)
        VALUES (1, 'admin', :password_hash, 'ADMIN', :status, :created_at)"""),
        {
            "password_hash": legacy_hash or "",
            "status": "ACTIVE" if legacy_hash else "SETUP_REQUIRED",
            "created_at": datetime.now(UTC),
        },
    )
    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_table(
        "password_reset_codes",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
    connection.execute(sa.text("UPDATE jobs SET owner_id = 1 WHERE owner_id IS NULL"))
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column("owner_id", existing_type=sa.Integer(), nullable=False)
        batch.create_index("ix_jobs_owner_id", ["owner_id"])
        batch.create_foreign_key("fk_jobs_owner_id_users", "users", ["owner_id"], ["id"])
    # Old sessions cannot be trusted after the cookie/auth model changes.
    op.drop_table("admin_sessions")
    op.drop_table("admin_accounts")


def downgrade() -> None:
    raise RuntimeError("Restore a pre-upgrade database backup; dropping user ownership is unsafe")
