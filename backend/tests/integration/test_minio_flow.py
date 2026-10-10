"""Object backend smoke with mocked S3: PDF → publish → private API → review → ZIP → delete."""

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
import fitz
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.adapters.storage.minio_store import MinioObjectStore
from app.api.deps import get_container, get_file_store, get_job_repository, get_session
from app.api.v1.routers.auth import require_user
from app.config import Settings
from app.db.models import Base, Chapter, DiagramReviewItem, Job, SearchDocument, User
from app.domain.enums import JobStatus
from app.domain.results import PipelineContext
from app.main import create_app
from app.repositories.job_repository import JobRepository
from app.services.library_search import SearchIndexService
from app.services.minio_diagram_review import MinioDiagramReviewService
from app.services.pipeline.stages.publish_objects import PublishObjectsStage
from app.services.quality_review import content_hash
from app.services.upload_service import UploadService


async def test_minio_job_and_local_legacy_coexist(tmp_path: Path) -> None:
    with mock_aws():
        remote = MinioObjectStore("http://s3.amazonaws.com", "user", "secret", "books")
        remote.client.create_bucket(Bucket="books")
        local = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                User(id=2, username="alice", password_hash="hash", status="ACTIVE", role="USER")
            )
            await session.commit()
            settings = Settings(storage_backend="minio")
            tasks: list[str] = []
            pdf = fitz.open()
            pdf.new_page()
            content = pdf.tobytes()
            pdf.close()
            uploader = UploadService(
                local, JobRepository(session), settings, tasks.append, object_store=remote
            )
            job = await uploader.accept_upload(
                UploadFile(filename="book.pdf", file=io.BytesIO(content)), owner_id=2
            )
            assert job.storage_backend == "minio"
            assert job.stored_path.startswith("s3://books/2/")
            assert tasks == [job.id]
            assert not Path(local.get_upload_path(job.id, "book.pdf")).exists()
            staged = tmp_path / "stage"
            source_path = staged / "upload.pdf"
            await remote.download(job.stored_path, source_path)
            assert source_path.read_bytes() == content
            stage_store = LocalFileStore(str(staged / "uploads"), str(staged / "outputs"))
            marker = "a" * 32
            md = (
                f"# One\n\n<!-- pdf2md-review:{marker}:start -->\n"
                f"![Figure](../assets/figure.png)\n"
                f"<!-- pdf2md-review:{marker}:end -->\n"
            )
            md_path = await stage_store.save_output_text(job.id, "01_One/chapter.md", md)
            await stage_store.save_output_bytes(job.id, "assets/figure.png", b"image-data")
            archive = staged / "outputs" / f"{job.id}.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("01_One/chapter.md", md)
                output.writestr(
                    "chapters.json",
                    json.dumps({"chapters": [{"order": 1, "mermaid_count": 0, "word_count": 5}]}),
                )
                output.writestr("assets/figure.png", b"image-data")
            session.add(
                Chapter(
                    job_id=job.id,
                    order_index=1,
                    title="One",
                    folder_name="01_One",
                    md_path=md_path,
                    mermaid_count=0,
                    image_count=1,
                    word_count=5,
                )
            )
            session.add(
                DiagramReviewItem(
                    id=marker,
                    job_id=job.id,
                    chapter_order=1,
                    page_number=1,
                    asset_name="figure.png",
                )
            )
            await session.commit()
            ctx = PipelineContext(
                job_id=job.id,
                upload_path=str(source_path),
                output_dir=stage_store.get_output_root(job.id),
                settings=settings,
                data={"output_path": str(archive)},
            )
            stage = PublishObjectsStage(SimpleNamespace(), stage_store, remote, session)
            assert (await stage.execute(ctx))["objects"] == 3
            job.output_path = ctx.data["output_path"]
            job.status = JobStatus.PARTIAL
            await session.commit()
            assert job.chapters[0].md_path.startswith("s3://books/2/")
            assert await remote.exists(job.output_path)
            await SearchIndexService(session, local, remote).index_job(job.id)
            old_doc = await session.get(SearchDocument, (job.id, 1))
            assert old_doc is not None
            old_hash = old_doc.content_hash
            review = MinioDiagramReviewService(session, remote)
            changed = await review.decide(job.id, marker, "EDITED_MERMAID", "flowchart TD\nA --> B")
            assert changed.decision == "EDITED_MERMAID"
            revised_md = await remote.read_text(job.chapters[0].md_path)
            assert "```mermaid" in revised_md
            await session.refresh(old_doc)
            assert old_doc.content_hash == content_hash(revised_md) != old_hash
            assert job.output_path.endswith("download.zip")
            # A prior local job remains accessible after MinIO is selected for new jobs.
            legacy_path = tmp_path / "outputs" / "legacy.zip"
            legacy_path.parent.mkdir(exist_ok=True)
            with zipfile.ZipFile(legacy_path, "w") as output:
                output.writestr("test.md", "legacy")
            session.add(
                Job(
                    id="legacy",
                    owner_id=2,
                    storage_backend="local",
                    filename="old.pdf",
                    stored_path="/data/uploads/old.pdf",
                    status=JobStatus.SUCCEEDED,
                    output_path=str(legacy_path),
                )
            )
            await session.commit()
            app = create_app()
            app.dependency_overrides[get_container] = lambda: SimpleNamespace(
                object_store=remote, settings=settings, redis=fakeredis.aioredis.FakeRedis()
            )
            app.dependency_overrides[require_user] = lambda: User(
                id=2, username="alice", role="USER", status="ACTIVE"
            )

            async def override_session():
                yield session

            app.dependency_overrides[get_session] = override_session
            app.dependency_overrides[get_file_store] = lambda: local
            app.dependency_overrides[get_job_repository] = lambda: JobRepository(session)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                path = f"/api/v1/jobs/{job.id}"
                preview = await client.get(f"{path}/chapters/1/preview")
                assert preview.status_code == 200 and "```mermaid" in preview.text
                asset = await client.get(f"{path}/assets/figure.png")
                assert asset.status_code == 200 and asset.content == b"image-data"
                downloaded = await client.get(f"{path}/download")
                assert downloaded.status_code == 200
                with zipfile.ZipFile(io.BytesIO(downloaded.content)) as output:
                    assert b"```mermaid" in output.read("01_One/chapter.md")
                assert (await client.get("/api/v1/jobs/legacy/download")).status_code == 200
                assert (await client.delete(path)).status_code == 204
                assert not remote.client.list_objects_v2(Bucket="books", Prefix=f"2/{job.id}/").get(
                    "Contents"
                )
                assert legacy_path.exists()
        await engine.dispose()


async def test_failed_object_publication_does_not_publish_partial_files(
    tmp_path: Path, monkeypatch
) -> None:
    from app.domain.exceptions import StorageError

    with mock_aws():
        remote = MinioObjectStore("http://s3.amazonaws.com", "user", "secret", "books")
        remote.client.create_bucket(Bucket="books")
        local = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                User(id=3, username="bob", password_hash="hash", status="ACTIVE", role="USER")
            )
            input_path = tmp_path / "input.pdf"
            input_path.write_bytes(b"%PDF-1.7")
            original_uri = await remote.upload(
                input_path, remote.key(3, "failed", "upload/input.pdf")
            )
            session.add(
                Job(
                    id="failed",
                    owner_id=3,
                    storage_backend="minio",
                    filename="input.pdf",
                    stored_path=original_uri,
                    status=JobStatus.RUNNING,
                )
            )
            md_path = await local.save_output_text("failed", "01_One/chapter.md", "# First")
            await local.save_output_bytes("failed", "assets/figure.png", b"original")
            session.add(
                Chapter(
                    job_id="failed",
                    order_index=1,
                    title="One",
                    folder_name="01_One",
                    md_path=md_path,
                )
            )
            await session.commit()
            archive = tmp_path / "outputs" / "failed.zip"
            archive.write_bytes(b"ZIP")
            ctx = PipelineContext(
                job_id="failed",
                upload_path=str(input_path),
                output_dir=local.get_output_root("failed"),
                settings=Settings(),
                data={"output_path": str(archive)},
            )
            original_upload = remote.upload
            counter = 0

            async def fail_second(path, key):
                nonlocal counter
                counter += 1
                if counter == 2:
                    raise StorageError("Simulated object outage")
                return await original_upload(path, key)

            monkeypatch.setattr(remote, "upload", fail_second)
            stage = PublishObjectsStage(SimpleNamespace(), local, remote, session)
            import pytest

            with pytest.raises(StorageError, match="Simulated"):
                await stage.execute(ctx)
            assert not remote.client.list_objects_v2(Bucket="books", Prefix="3/failed/output/").get(
                "Contents"
            )
            assert await remote.exists(original_uri)
            assert (await session.get(Job, "failed")).output_path is None
        await engine.dispose()
