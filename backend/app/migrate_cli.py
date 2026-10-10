"""Safe migration entrypoint for empty or current pre-Alembic databases."""

import asyncio
import os
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings

BASELINE = "40f38ec4bfb1"
# Fail closed on an unexpected pre-Alembic schema instead of stamping it.
BASELINE_COLUMNS: dict[str, set[str]] = {
    "admin_accounts": {"id", "password_hash"},
    "admin_sessions": {"token_hash", "csrf_token", "expires_at"},
    "bootstrap_codes": {"id", "token_hash", "expires_at"},
    "runtime_settings": {"key", "value"},
    "jobs": {
        "id",
        "filename",
        "stored_path",
        "status",
        "page_count",
        "chapter_count",
        "output_path",
        "error",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    },
    "job_stages": {
        "id",
        "job_id",
        "stage_name",
        "status",
        "started_at",
        "finished_at",
        "error",
        "metrics",
    },
    "chapters": {
        "id",
        "job_id",
        "order_index",
        "title",
        "folder_name",
        "md_path",
        "mermaid_count",
        "image_count",
        "word_count",
    },
    "progress_events": {"id", "job_id", "ts", "stage", "percent", "message"},
    "diagram_review_items": {
        "id",
        "job_id",
        "chapter_order",
        "page_number",
        "occurrence_index",
        "position_y",
        "asset_name",
        "reason",
        "decision",
        "mermaid_code",
        "reviewed_at",
    },
}


def inspect_existing(connection: Connection) -> bool:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables or not tables:
        return False
    if tables != BASELINE_COLUMNS.keys():
        raise RuntimeError(
            "Unknown pre-Alembic database schema; restore/upgrade to the current "
            "Phase 4 release first (no changes were made)"
        )
    for table, columns in BASELINE_COLUMNS.items():
        if {column["name"] for column in inspector.get_columns(table)} != columns:
            raise RuntimeError(f"Unexpected columns in {table}; migration refused")
    return True


async def needs_stamp(url: str) -> bool:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(inspect_existing)
    finally:
        await engine.dispose()


async def check_database(url: str) -> str:
    """Read-only migration preflight; never stamps or alters schema."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            stamp = await connection.run_sync(inspect_existing)
            tables = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
            if stamp:
                return f"Unversioned Phase 4 schema: safe to stamp {BASELINE} and upgrade"
            if "alembic_version" not in tables:
                return "Empty database: baseline and subsequent migrations required"
            version = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar()
            if not version:
                raise RuntimeError("Alembic version table is empty; inspect before upgrading")
            return f"Current database revision: {version}"
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    config = Config("alembic.ini")
    url = os.getenv("ALEMBIC_DATABASE_URL", get_settings().database_url)
    if argv:
        if argv != ["check"]:
            raise SystemExit("Usage: python -m app.migrate_cli [check]")
        print(asyncio.run(check_database(url)))
        return
    if asyncio.run(needs_stamp(url)):
        command.stamp(config, BASELINE)
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
