"""Direct edit works without PDF/page map; revisions update ZIP, title, search and Undo."""

import json
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.api.deps import get_container, get_file_store, get_job_repository, get_session
from app.api.v1.routers.auth import COOKIE, digest
from app.config import Settings
from app.db.models import Base, Chapter, Job, ShelfBook, ShelfChapter, User, UserSession, utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import ConflictError, ValidationError
from app.main import create_app
from app.repositories.job_repository import JobRepository
from app.services.library_search import SearchIndexService, search_library
from app.services.quality_review import QualityReviewService, content_hash, validate_edit


async def test_direct_editor_revises_legacy_book_without_source_or_map(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    job_id = "legacy-edit"
    md = (
        '---\ntitle: "Old"\norder: 1\nsource_pages: 1-1\n---\n\n'
        "# Old\n\nAncient text.\n\n<small>Legacy formatting</small>\n"
    )
    md_path = await store.save_output_text(job_id, "01_Old/chapter.md", md)
    manifest = {"chapters": [{"order": 1, "title": "Old", "word_count": 3}]}
    archive_path = Path(store.get_output_root(job_id)).parent / f"{job_id}.zip"
    with zipfile.ZipFile(archive_path, "w") as zipped:
        zipped.writestr("01_Old/chapter.md", md)
        zipped.writestr("chapters.json", json.dumps(manifest))
        zipped.writestr("assets/image.png", b"\x89PNG")
    redis = fakeredis.aioredis.FakeRedis()
    async with factory() as session:
        session.add_all(
            [
                User(id=1, username="owner", password_hash="x", role="USER", status="ACTIVE"),
                User(id=2, username="stranger", password_hash="x", role="USER", status="ACTIVE"),
                UserSession(
                    user_id=1,
                    token_hash=digest("owner-token"),
                    csrf_token="owner-csrf",
                    expires_at=utcnow() + timedelta(hours=1),
                ),
                UserSession(
                    user_id=2,
                    token_hash=digest("other-token"),
                    csrf_token="other-csrf",
                    expires_at=utcnow() + timedelta(hours=1),
                ),
                ShelfBook(
                    id="snapshot-1",
                    owner_id=1,
                    title="Old",
                    source_kind="CONVERTED",
                    storage_backend="local",
                    chapters=[
                        ShelfChapter(
                            order_index=1,
                            title="Old",
                            relative_path="01_Old/chapter.md",
                        )
                    ],
                ),
                Job(
                    id=job_id,
                    owner_id=1,
                    filename="old.pdf",
                    stored_path="missing-source.pdf",
                    status=JobStatus.SUCCEEDED,
                    page_count=1,
                    chapter_count=1,
                    output_path=str(archive_path),
                    chapters=[
                        Chapter(
                            order_index=1,
                            title="Old",
                            folder_name="01_Old",
                            md_path=md_path,
                            word_count=3,
                        )
                    ],
                ),
            ]
        )
        await session.commit()
        await SearchIndexService(session, store, None).index_job(job_id)
        app = create_app()
        container = SimpleNamespace(settings=Settings(), redis=redis, object_store=None)
        app.dependency_overrides[get_container] = lambda: container
        app.dependency_overrides[get_file_store] = lambda: store
        app.dependency_overrides[get_job_repository] = lambda: JobRepository(session)

        async def override_session():
            yield session

        app.dependency_overrides[get_session] = override_session
        base = f"/api/v1/jobs/{job_id}/chapters/1/edit"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(base)).status_code == 401
            client.cookies.set(COOKIE, "other-token", path="/api/v1")
            assert (await client.get(base)).status_code == 404
            assert (
                await client.put(
                    base,
                    json={"expected_hash": "0" * 64, "markdown": "hi"},
                    headers={"X-CSRF-Token": "other-csrf"},
                )
            ).status_code == 404
            client.cookies.set(COOKIE, "owner-token", path="/api/v1")
            loaded = (await client.get(base)).json()
            assert loaded["markdown"] == md
            updated = md.replace("# Old", "# New Chapter").replace(
                "Ancient text.",
                "A corrected table.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n"
                "```mermaid\nflowchart TD\nA --> B\n```\n\n"
                "![Asset](../assets/image.png)",
            )
            assert (
                await client.put(base, json={"expected_hash": loaded["hash"], "markdown": updated})
            ).status_code == 403  # CSRF
            headers = {"X-CSRF-Token": "owner-csrf"}
            missing_asset = await client.put(
                base,
                json={
                    "expected_hash": loaded["hash"],
                    "markdown": updated.replace("image.png", "missing.png"),
                },
                headers=headers,
            )
            assert missing_asset.status_code == 400
            saved = await client.put(
                base, json={"expected_hash": loaded["hash"], "markdown": updated}, headers=headers
            )
            assert saved.status_code == 200, saved.text
            current = (await client.get(base)).json()
            assert current["hash"] == saved.json()["hash"]
            assert current["can_undo"] is True
            assert 'title: "New Chapter"' in current["markdown"]
            job = await JobRepository(session).get(job_id)
            assert job is not None
            assert job.chapters[0].title == "New Chapter"
            assert job.chapters[0].mermaid_count == 1
            assert job.chapters[0].folder_name == "01_Old"
            shelf = await session.get(ShelfBook, "snapshot-1")
            assert shelf is not None and shelf.title == "Old"
            assert shelf.chapters[0].title == "Old"
            assert await store.exists(job.output_path)
            with zipfile.ZipFile(job.output_path) as zipped:
                assert zipped.read("01_Old/chapter.md").decode() == current["markdown"]
                revised_manifest = json.loads(zipped.read("chapters.json"))
                assert revised_manifest["chapters"][0]["title"] == "New Chapter"
                assert revised_manifest["chapters"][0]["mermaid_count"] == 1
                assert zipped.read("assets/image.png") == b"\x89PNG"
            assert (await search_library(session, 1, False, "corrected table", 10))["results"]
            assert not (await search_library(session, 1, False, "Ancient text", 10))["results"]
            assert (
                await client.put(
                    base,
                    json={"expected_hash": loaded["hash"], "markdown": updated},
                    headers=headers,
                )
            ).status_code == 409
            undone = await client.post(
                base + "/undo", json={"expected_hash": current["hash"]}, headers=headers
            )
            assert undone.status_code == 200, undone.text
            restored = (await client.get(base)).json()
            assert restored["markdown"] == md
            assert restored["can_undo"] is False
            assert (await search_library(session, 1, False, "Ancient text", 10))["results"]
            stable_path = Path(job.output_path)
            missing_path = stable_path.with_suffix(".missing")
            stable_path.rename(missing_path)
            try:
                failed = await client.put(
                    base,
                    json={
                        "expected_hash": restored["hash"],
                        "markdown": md.replace("Ancient", "Fresh"),
                    },
                    headers=headers,
                )
                assert failed.status_code == 409
                refreshed_job = await JobRepository(session).get(job_id)
                assert refreshed_job is not None and refreshed_job.output_path == str(stable_path)
                assert (await client.get(base)).json()["markdown"] == md
            finally:
                missing_path.rename(stable_path)
    await engine.dispose()


def test_full_edit_validates_mermaid_and_protects_review_regions() -> None:
    old = (
        '---\ntitle: "One"\n---\n\n# One\n\n'
        "<!-- pdf2md-review:abc:start -->\n![Old](../assets/a.png)\n"
        "<!-- pdf2md-review:abc:end -->\n"
    )
    full = old + "\n```mermaid\nflowchart TD\nA --> B\n```\n"
    validate_edit(old, full, full_content=True)
    inherited = old + "\n<small>Existing formatting</small>\n<!-- legacy OCR note -->\n"
    validate_edit(inherited, inherited.replace("# One", "# Corrected"), full_content=True)
    validate_edit(
        inherited, inherited.replace("<small>", "").replace("</small>", ""), full_content=True
    )
    for bad_legacy_change in (
        inherited + "<script>alert(1)</script>",
        inherited.replace("<small>", "<strong>"),
        inherited + "<!-- new note -->",
        inherited + "<!DOCTYPE html>",
    ):
        with pytest.raises(ValidationError, match="New raw HTML"):
            validate_edit(inherited, bad_legacy_change, full_content=True)
    for changed in (
        old.replace("![Old]", "![Other]"),
        old + '\n```mermaid\n%%{init: {"securityLevel": "loose"}}\nX\n```',
        old + "\n```mermaid\nunfinished",
        old + "\n<script>alert(1)</script>",
        old + "\n[unsafe](javascript:alert(1))",
    ):
        try:
            validate_edit(old, changed, full_content=True)
        except ValidationError:
            pass
        else:
            raise AssertionError("Unsafe edit unexpectedly passed validation")


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED])
async def test_direct_edit_requires_completed_job(tmp_path: Path, status: JobStatus) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    md = "# Chapter\n\nText."
    path = await store.save_output_text("unfinished", "01/chapter.md", md)
    async with factory() as session:
        session.add(
            Job(
                id="unfinished",
                status=status,
                filename="book.pdf",
                stored_path="missing.pdf",
                chapters=[Chapter(order_index=1, title="Chapter", folder_name="01", md_path=path)],
            )
        )
        await session.commit()
        service = QualityReviewService(session, store, None)
        with pytest.raises(ConflictError):
            await service.edit(
                "unfinished", 1, content_hash(md), "# Chapter\n\nFixed.", actor_id=1, direct=True
            )
    await engine.dispose()
