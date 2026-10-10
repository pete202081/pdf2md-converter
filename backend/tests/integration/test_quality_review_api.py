"""Private page rendering, honest flags, revision-safe editing and undo."""

import json
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
import pymupdf
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.api.deps import get_container, get_file_store, get_job_repository, get_session
from app.api.v1.routers.auth import COOKIE, digest
from app.config import Settings
from app.db.models import (
    Base,
    Chapter,
    ChapterEdit,
    DiagramReviewItem,
    Job,
    PageQualityFlag,
    User,
    UserSession,
    utcnow,
)
from app.domain.enums import JobStatus
from app.main import create_app
from app.repositories.job_repository import JobRepository
from app.services.quality_review import QualityReviewService, content_hash


async def test_quality_review_owner_pages_flags_edit_and_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    job_id = "quality-1"
    pdf = pymupdf.open()
    for _ in range(2):
        pdf.new_page().insert_text((30, 50), "Source page")
    source = Path(store.get_upload_path(job_id, "book.pdf"))
    source.parent.mkdir(parents=True)
    pdf.save(source)
    pdf.close()
    anchored = (
        "<!-- pdf2md-review:aaa:start -->\n"
        "![Diagram](../assets/image.png)\n"
        "<!-- pdf2md-review:aaa:end -->"
    )
    md = (
        '---\ntitle: "One"\norder: 1\nsource_pages: 1-2\n---\n\n'
        f"# One\n\nOriginal text.\n\n{anchored}\n"
    )
    md_path = await store.save_output_text(job_id, "01_One/chapter.md", md)
    await store.save_output_bytes(job_id, "assets/image.png", b"\x89PNG\r\n\x1a\n")
    manifest = {"chapters": [{"order": 1, "source_pages": [1, 2], "word_count": len(md.split())}]}
    await store.save_output_text(job_id, "chapters.json", json.dumps(manifest))
    archive = tmp_path / "outputs" / f"{job_id}.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("01_One/chapter.md", md)
        zipped.writestr("chapters.json", json.dumps(manifest))
        zipped.writestr("assets/image.png", b"\x89PNG\r\n\x1a\n")
    redis = fakeredis.aioredis.FakeRedis()
    async with factory() as session:
        session.add_all(
            [
                User(id=1, username="admin", password_hash="hash", role="ADMIN", status="ACTIVE"),
                User(id=2, username="owner", password_hash="hash", role="USER", status="ACTIVE"),
                User(id=3, username="stranger", password_hash="hash", role="USER", status="ACTIVE"),
                UserSession(
                    user_id=2,
                    token_hash=digest("owner-token"),
                    csrf_token="csrf-owner",
                    expires_at=utcnow() + timedelta(hours=1),
                ),
                UserSession(
                    user_id=3,
                    token_hash=digest("other-token"),
                    csrf_token="csrf-other",
                    expires_at=utcnow() + timedelta(hours=1),
                ),
            ]
        )
        session.add(
            Job(
                id=job_id,
                owner_id=2,
                filename="book.pdf",
                stored_path=str(source),
                status=JobStatus.PARTIAL,
                page_count=2,
                chapter_count=1,
                quality_inventory_ready=True,
                output_path=str(archive),
                chapters=[
                    Chapter(
                        order_index=1,
                        title="One",
                        folder_name="01_One",
                        md_path=md_path,
                        word_count=len(md.split()),
                    )
                ],
                review_items=[
                    DiagramReviewItem(
                        id="aaa", chapter_order=1, page_number=1, asset_name="image.png"
                    )
                ],
            )
        )
        session.add(
            PageQualityFlag(
                job_id=job_id,
                page_number=1,
                chapter_order=1,
                code="OCR_USED",
                message="Scanned page: OCR fallback was used",
            )
        )
        await session.commit()
        app = create_app()
        container = SimpleNamespace(settings=Settings(), redis=redis, object_store=None)
        app.dependency_overrides[get_container] = lambda: container
        app.dependency_overrides[get_file_store] = lambda: store
        app.dependency_overrides[get_job_repository] = lambda: JobRepository(session)

        async def override_session():
            yield session

        app.dependency_overrides[get_session] = override_session
        base = f"/api/v1/jobs/{job_id}/quality"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(base)).status_code == 401
            client.cookies.set(COOKIE, "other-token", path="/api/v1")
            assert (await client.get(base)).status_code == 404
            assert (await client.get(f"{base}/chapters/1/pages/1.png")).status_code == 404
            client.cookies.set(COOKIE, "owner-token", path="/api/v1")
            result = (await client.get(base)).json()
            assert result["inventory_ready"] is True
            assert result["chapters"] == [{"order": 1, "source_pages": [1, 2]}]
            assert result["flags"][0]["reviewed_at"] is None
            assert (await client.get(f"{base}/chapters/1/pages/0.png")).status_code == 404
            assert (await client.get(f"{base}/chapters/1/pages/1.png")).content.startswith(
                b"\x89PNG"
            )
            assert (await client.put(f"{base}/pages/1")).status_code == 403
            headers = {"X-CSRF-Token": "csrf-owner"}
            marked = await client.put(f"{base}/pages/1", headers=headers)
            assert marked.status_code == 200 and marked.json()["flags"][0]["reviewed_at"]
            editor = (await client.get(f"{base}/chapters/1")).json()
            assert editor["markdown"] == md
            updated = md.replace("Original text.", "Corrected table row.")
            assert (
                await client.put(
                    f"{base}/chapters/1",
                    headers=headers,
                    json={
                        "expected_hash": editor["hash"],
                        "markdown": updated.replace(anchored, ""),
                    },
                )
            ).status_code == 400
            assert (
                await client.put(
                    f"{base}/chapters/1",
                    headers=headers,
                    json={
                        "expected_hash": editor["hash"],
                        "markdown": updated.replace("image.png", "missing.png"),
                    },
                )
            ).status_code == 400
            saved = await client.put(
                f"{base}/chapters/1",
                headers=headers,
                json={"expected_hash": editor["hash"], "markdown": updated},
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["hash"] != editor["hash"]
            assert (await client.get(base)).json()["flags"][0]["reviewed_at"] is None
            assert (
                await client.put(
                    f"{base}/chapters/1",
                    headers=headers,
                    json={"expected_hash": editor["hash"], "markdown": updated},
                )
            ).status_code == 409
            download = await client.get(f"/api/v1/jobs/{job_id}/download")
            assert download.status_code == 200
            current = await session.get(Job, job_id)
            assert current is not None and current.output_path is not None
            with zipfile.ZipFile(current.output_path) as zipped:
                assert zipped.read("01_One/chapter.md").decode() == updated
                assert json.loads(zipped.read("chapters.json"))["chapters"][0]["word_count"] == len(
                    updated.split()
                )
            undo = await client.post(
                f"{base}/chapters/1/undo",
                headers=headers,
                json={"expected_hash": saved.json()["hash"]},
            )
            assert undo.status_code == 200, undo.text
            assert (await client.get(f"{base}/chapters/1")).json()["markdown"] == md
            assert (
                await client.post(
                    f"{base}/chapters/1/undo",
                    headers=headers,
                    json={"expected_hash": undo.json()["hash"]},
                )
            ).status_code == 409
            edit_rows = (
                await session.scalars(select(ChapterEdit).where(ChapterEdit.job_id == job_id))
            ).all()
            assert len(edit_rows) == 1 and edit_rows[0].undone_at is not None
            assert edit_rows[0].undone_by_id == 2
            # Chapter edit followed by diagram review must preserve BOTH changes;
            # undoing across the newer diagram decision must refuse a stale hash.
            second = await client.put(
                f"{base}/chapters/1",
                headers=headers,
                json={"expected_hash": undo.json()["hash"], "markdown": updated},
            )
            assert second.status_code == 200, second.text
            diagram = await client.put(
                f"/api/v1/jobs/{job_id}/reviews/aaa",
                headers=headers,
                json={"decision": "EDITED_MERMAID", "mermaid_code": "flowchart TD\nA --> B"},
            )
            assert diagram.status_code == 200, diagram.text
            assert (
                await client.post(
                    f"{base}/chapters/1/undo",
                    headers=headers,
                    json={"expected_hash": second.json()["hash"]},
                )
            ).status_code == 409
            newest = (await client.get(f"{base}/chapters/1")).json()["markdown"]
            assert "Corrected table row." in newest and "```mermaid" in newest
            old_download = current.output_path
            old_chapter = current.chapters[0].md_path
            import app.services.quality_review as quality_module

            def fail_zip(*_args: object) -> None:
                raise OSError("simulated archive failure")

            with monkeypatch.context() as patch:
                patch.setattr(quality_module, "write_revision", fail_zip)
                with pytest.raises(OSError, match="simulated archive failure"):
                    await QualityReviewService(session, store, None).edit(
                        job_id,
                        1,
                        content_hash(newest),
                        newest.replace("Corrected table row.", "Another correction."),
                        2,
                    )
            await session.refresh(current, attribute_names=["output_path", "chapters"])
            assert current.output_path == old_download
            assert current.chapters[0].md_path == old_chapter

    await redis.aclose()
    await engine.dispose()
