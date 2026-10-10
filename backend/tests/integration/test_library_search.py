"""Private search, current revisions, and resumable backfill on SQLite."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.api.deps import get_container, get_session
from app.api.v1.routers.auth import COOKIE, digest
from app.config import Settings
from app.db.models import Base, Chapter, Job, SearchDocument, User, UserSession, utcnow
from app.domain.enums import JobStatus
from app.main import create_app
from app.repositories.job_repository import JobRepository
from app.services.library_search import (
    SearchIndexService,
    search_library,
    searchable_text,
    source_range,
)


def test_normalization_and_truthful_ranges() -> None:
    md = (
        '---\ntitle: "Lost"\nsource_pages: 4-9\n---\n<!-- hidden -->\n'
        "| First | Total |\n|:---|:---|\n| Alice | 55\x00 |\n```mermaid\nA --> B\n```"
    )
    text = searchable_text(md)
    assert "Alice" in text and "55" in text and "hidden" not in text
    assert "mermaid" not in text and "source_pages" not in text
    assert "\x00" not in text
    assert source_range(md) == (4, 9)
    assert source_range("# title\nNo metadata") == (None, None)
    assert source_range("---\nsource_pages: 0-99\n---\nHi") == (None, None)


async def test_search_api_ownership_csrf_revision_failure_and_deletion(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    redis = fakeredis.aioredis.FakeRedis()
    app = create_app()
    container = SimpleNamespace(redis=redis, settings=Settings())
    app.dependency_overrides[get_container] = lambda: container

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    md = (
        '---\ntitle: "Tables"\nsource_pages: 2-4\n---\n# Tables\n'
        "| Price |\n| --- |\n| Needle55\x00 |"
    )
    async with factory() as session:
        for id_, role in ((1, "ADMIN"), (2, "USER"), (3, "USER")):
            session.add(
                User(
                    id=id_, username=f"user{id_}", role=role, status="ACTIVE", password_hash="hash"
                )
            )
            session.add(
                UserSession(
                    user_id=id_,
                    token_hash=digest(f"token{id_}"),
                    csrf_token=f"csrf{id_}",
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
        for job_id, owner, status in (
            ("own", 2, JobStatus.SUCCEEDED),
            ("other", 3, JobStatus.PARTIAL),
            ("working", 2, JobStatus.RUNNING),
            ("legacy", 2, JobStatus.SUCCEEDED),
        ):
            path = await store.save_output_text(job_id, "01_Tables/chapter.md", md)
            session.add(
                Job(
                    id=job_id,
                    owner_id=owner,
                    filename=f"{job_id}.pdf",
                    stored_path="unused",
                    status=status,
                    chapters=[
                        Chapter(
                            order_index=1, title="Tables", folder_name="01_Tables", md_path=path
                        )
                    ],
                )
            )
        await session.commit()
        indexer = SearchIndexService(session, store, None)
        for job_id in ("own", "other", "working"):
            await indexer.index_job(job_id)
        assert (
            await session.scalar(select(SearchDocument).where(SearchDocument.job_id == "working"))
            is None
        )
        assert await indexer.index_job("own") == 1
        assert (
            len(
                (
                    await session.scalars(
                        select(SearchDocument).where(SearchDocument.job_id == "own")
                    )
                ).all()
            )
            == 1
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = "/api/v1/search"
        assert (await client.post(url, json={"query": "Needle55"})).status_code == 401
        client.cookies.set(COOKIE, "token2", path="/api/v1")
        assert (await client.post(url, json={"query": "Needle55"})).status_code == 403
        headers = {"X-CSRF-Token": "csrf2"}
        assert (
            await client.post(url, json={"query": "x", "limit": 1}, headers=headers)
        ).status_code == 422
        assert (
            await client.post(url, json={"query": "x" * 101}, headers=headers)
        ).status_code == 422
        assert (
            await client.post(url, json={"query": "Needle55", "limit": 51}, headers=headers)
        ).status_code == 422
        injected = await client.post(url, json={"query": "%' OR 1=1 --"}, headers=headers)
        assert injected.status_code == 200 and injected.json()["results"] == []
        found = await client.post(url, json={"query": "Needle55"}, headers=headers)
        assert found.status_code == 200
        assert found.json()["pending_books"] == 1
        assert [item["job_id"] for item in found.json()["results"]] == ["own"]
        assert found.json()["results"][0]["first_page"] == 2
        assert found.json()["results"][0]["last_page"] == 4
        assert "Needle55" in found.json()["results"][0]["excerpt"]
        client.cookies.set(COOKIE, "token1", path="/api/v1")
        admin = await client.post(
            url, json={"query": "Needle55"}, headers={"X-CSRF-Token": "csrf1"}
        )
        assert {hit["job_id"] for hit in admin.json()["results"]} == {"own", "other"}
        assert admin.json()["pending_books"] == 1

    async with factory() as session:
        job = await session.get(Job, "own")
        chapter = await session.scalar(select(Chapter).where(Chapter.job_id == "own"))
        assert job and chapter
        await SearchIndexService(session, store, None).revision(
            job, chapter, md.replace("Needle55", "Updated77")
        )
        await session.commit()
        found = await search_library(session, 2, False, "Needle55", 10)
        assert not found["results"]
        assert len((await search_library(session, 2, False, "Updated77", 10))["results"]) == 1
        import app.services.library_search as search_module

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                search_module,
                "make_document",
                lambda *_: (_ for _ in ()).throw(OSError("index unavailable")),
            )
            # The chapter and existing ZIP remain available; all stale search hits are hidden.
            await SearchIndexService(session, store, None).revision(job, chapter, md)
            await session.commit()
        assert job.search_index_state == "ERROR"
        assert (await search_library(session, 2, False, "Updated77", 10))["pending_books"] == 2
        await JobRepository(session).delete("own")
        assert (
            await session.scalar(select(SearchDocument).where(SearchDocument.job_id == "own"))
            is None
        )
    await redis.aclose()
    await engine.dispose()


async def test_reindex_continues_after_missing_chapter(tmp_path: Path, monkeypatch) -> None:
    from app import search_reindex_cli
    from app.db.session import Database

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'search.sqlite'}")
    await database.init_models()
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    async with database.session() as session:
        session.add(
            User(id=1, username="admin", role="ADMIN", status="ACTIVE", password_hash="hash")
        )
        for id_, exists in (("a_missing", False), ("b_good", True)):
            path = store.get_output_path(id_, "01_One/chapter.md")
            if exists:
                path = await store.save_output_text(
                    id_, "01_One/chapter.md", "---\nsource_pages: 1-1\n---\nTinyneedle"
                )
            session.add(
                Job(
                    id=id_,
                    owner_id=1,
                    filename=f"{id_}.pdf",
                    stored_path="unused",
                    status=JobStatus.SUCCEEDED,
                    chapters=[
                        Chapter(order_index=1, title="One", folder_name="01_One", md_path=path)
                    ],
                )
            )
        await session.commit()

    # The CLI owns/cleans its container. Reopen for assertions below.
    class FakeContainer:
        def __init__(self):
            self.database = database
            self.file_store = store
            self.object_store = None

        async def close(self):
            pass

    monkeypatch.setattr(search_reindex_cli, "build_container", lambda settings: FakeContainer())
    assert await search_reindex_cli.reindex(batch_size=1) == (1, 1)
    async with database.session() as session:
        assert (await session.get(Job, "a_missing")).search_index_state == "ERROR"
        assert (await session.get(Job, "b_good")).search_index_state == "READY"
    # A retry is safe and does not duplicate rows.
    await store.save_output_text(
        "a_missing", "01_One/chapter.md", "---\nsource_pages: 1-1\n---\nNewneedle"
    )
    assert await search_reindex_cli.reindex(batch_size=1) == (1, 0)
    assert await search_reindex_cli.reindex(batch_size=1, force=True) == (2, 0)
    async with database.session() as session:
        assert len((await session.scalars(select(SearchDocument))).all()) == 2
    await database.close()
