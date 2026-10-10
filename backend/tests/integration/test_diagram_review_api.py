"""Review HTTP contract: authorization, CSRF, input validation, ZIP and cleanup."""

import json
import zipfile
from datetime import timedelta
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.api.deps import get_file_store, get_job_repository, get_session
from app.api.v1.routers.admin import COOKIE, digest
from app.db.models import AdminSession, Base, Chapter, DiagramReviewItem, Job, utcnow
from app.domain.enums import JobStatus
from app.main import create_app
from app.repositories.job_repository import JobRepository


async def test_review_actions_are_admin_only_and_update_download(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    job_id = "job-review"
    item_id = "a" * 32
    md = (
        f"# One\n\n<!-- pdf2md-review:{item_id}:start -->\n"
        f"![Figure](../assets/img_p1_x1.png)\n"
        f"<!-- pdf2md-review:{item_id}:end -->\n"
    )
    chapter_path = await store.save_output_text(job_id, "01_One/chapter.md", md)
    await store.save_output_bytes(job_id, "assets/img_p1_x1.png", b"\x89PNG\r\n\x1a\n")
    manifest = {"chapters": [{"order": 1, "mermaid_count": 0, "word_count": 10}]}
    original_zip = tmp_path / "outputs" / f"{job_id}.zip"
    with zipfile.ZipFile(original_zip, "w") as archive:
        archive.writestr("01_One/chapter.md", md)
        archive.writestr("chapters.json", json.dumps(manifest))
        archive.writestr("assets/img_p1_x1.png", b"\x89PNG\r\n\x1a\n")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job = Job(
            id=job_id,
            filename="sample.pdf",
            stored_path="unused.pdf",
            status=JobStatus.PARTIAL,
            output_path=str(original_zip),
        )
        job.chapters.append(
            Chapter(
                job_id=job_id,
                order_index=1,
                title="One",
                folder_name="01_One",
                md_path=chapter_path,
                mermaid_count=0,
                image_count=1,
                word_count=10,
            )
        )
        job.review_items.append(
            DiagramReviewItem(
                id=item_id,
                job_id=job_id,
                chapter_order=1,
                page_number=1,
                asset_name="img_p1_x1.png",
            )
        )
        session.add(job)
        session.add(
            AdminSession(
                token_hash=digest("admin-session"),
                csrf_token="csrf-secret",
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        await session.commit()
        app = create_app()

        async def override_session():
            yield session

        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_file_store] = lambda: store
        app.dependency_overrides[get_job_repository] = lambda: JobRepository(session)
        url = f"/api/v1/jobs/{job_id}/reviews"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listed = await client.get(url)
            assert listed.status_code == 200 and listed.json()[0]["asset_name"] == "img_p1_x1.png"
            assert (
                await client.put(f"{url}/{item_id}", json={"decision": "ACCEPTED_IMAGE"})
            ).status_code == 401
            client.cookies.set(COOKIE, "admin-session", path="/api/v1")
            assert (
                await client.put(f"{url}/{item_id}", json={"decision": "ACCEPTED_IMAGE"})
            ).status_code == 403
            headers = {"X-CSRF-Token": "csrf-secret"}
            assert (
                await client.put(
                    f"{url}/{'b' * 32}", headers=headers, json={"decision": "ACCEPTED_IMAGE"}
                )
            ).status_code == 404
            rejected = await client.put(
                f"{url}/{item_id}",
                headers=headers,
                json={
                    "decision": "EDITED_MERMAID",
                    "mermaid_code": "flowchart TD\nA[<script>bad</script>]",
                },
            )
            assert rejected.status_code == 400
            accepted = await client.put(
                f"{url}/{item_id}", headers=headers, json={"decision": "ACCEPTED_IMAGE"}
            )
            assert accepted.status_code == 200 and accepted.json()["decision"] == "ACCEPTED_IMAGE"
            assert job.output_path == str(original_zip)
            edited = await client.put(
                f"{url}/{item_id}",
                headers=headers,
                json={"decision": "EDITED_MERMAID", "mermaid_code": "flowchart TD\nA --> B"},
            )
            assert edited.status_code == 200
            assert job.output_path != str(original_zip)
            details = await client.get(f"/api/v1/jobs/{job_id}")
            assert details.status_code == 200
            assert details.json()["review_total"] == 1
            assert details.json()["review_pending"] == 0
            library = await client.get("/api/v1/jobs")
            assert library.json()[0]["review_pending"] == 0
            downloaded = await client.get(f"/api/v1/jobs/{job_id}/download")
            assert downloaded.status_code == 200
            with zipfile.ZipFile(Path(job.output_path)) as archive:
                assert "```mermaid" in archive.read("01_One/chapter.md").decode()
            assert (await client.delete(f"/api/v1/jobs/{job_id}")).status_code == 204
            assert not Path(store.get_output_root(job_id)).exists()
            assert not original_zip.exists()
    await engine.dispose()
