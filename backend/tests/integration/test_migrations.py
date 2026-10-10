"""The versioned baseline must preserve existing pre-Alembic installations."""

import asyncio
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from app.migrate_cli import main, needs_stamp


def test_existing_database_stamps_then_backfills_without_data_loss(tmp_path, monkeypatch) -> None:
    db = tmp_path / "legacy.sqlite"
    url = f"sqlite+aiosqlite:///{db}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "40f38ec4bfb1")
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TABLE alembic_version")
        connection.execute("INSERT INTO admin_accounts VALUES (1, 'existing-argon2-hash')")
        connection.execute("INSERT INTO runtime_settings VALUES ('vlm_provider', 'cloud')")
        connection.execute("""INSERT INTO jobs
            (id,filename,stored_path,status,page_count,chapter_count,output_path,error,created_at,updated_at)
            VALUES ('old','book.pdf','/data/uploads/old.pdf','SUCCEEDED',1,1,
                    '/data/outputs/old.zip',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        connection.execute("""INSERT INTO diagram_review_items
            (id,job_id,chapter_order,page_number,occurrence_index,position_y,asset_name,reason,decision)
            VALUES ('review','old',1,1,0,0.2,'diagram.png','fallback','PENDING')""")
    main([])
    main([])  # repeatable, no duplicate data
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "76b8e4d18f01",
        )
        assert connection.execute("SELECT password_hash FROM users WHERE id=1").fetchone() == (
            "existing-argon2-hash",
        )
        assert connection.execute(
            "SELECT owner_id,stored_path,output_path FROM jobs"
        ).fetchone() == (1, "/data/uploads/old.pdf", "/data/outputs/old.zip")
        assert connection.execute("SELECT storage_backend FROM jobs").fetchone() == ("local",)
        assert connection.execute("SELECT quality_inventory_ready FROM jobs").fetchone() == (0,)
        assert connection.execute("SELECT search_index_state FROM jobs").fetchone() == ("PENDING",)
        assert connection.execute("SELECT count(*) FROM search_documents").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM page_quality_flags").fetchone() == (0,)
        assert connection.execute("SELECT decision FROM diagram_review_items").fetchone() == (
            "PENDING",
        )
        assert connection.execute("SELECT value FROM runtime_settings").fetchone() == ("cloud",)


def test_unknown_unversioned_schema_refused_without_changes(tmp_path) -> None:
    db = tmp_path / "unknown.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE jobs (id TEXT)")
    with pytest.raises(RuntimeError, match="Unknown pre-Alembic"):
        asyncio.run(needs_stamp(f"sqlite+aiosqlite:///{db}"))
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("jobs",)]
