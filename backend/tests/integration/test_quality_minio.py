"""Private MinIO-backed source page and revision publication (mocked S3)."""

import json
import tempfile
import zipfile
from pathlib import Path

import pymupdf
from moto import mock_aws
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.adapters.storage.minio_store import MinioObjectStore
from app.db.models import Base, Chapter, Job, User
from app.domain.enums import JobStatus
from app.services.library_search import SearchIndexService, search_library
from app.services.quality_review import QualityReviewService, content_hash


async def test_minio_quality_source_edit_undo_keeps_prior_revision(tmp_path: Path) -> None:
    with mock_aws():
        remote = MinioObjectStore("http://s3.amazonaws.com", "user", "secret", "books")
        remote.client.create_bucket(Bucket="books")
        local = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
        pdf = pymupdf.open()
        pdf.new_page().insert_text((30, 50), "Private source")
        source = tmp_path / "book.pdf"
        pdf.save(source)
        pdf.close()
        original_uri = await remote.upload(source, remote.key(2, "s3quality", "upload/book.pdf"))
        md = '---\ntitle: "One"\norder: 1\nsource_pages: 1-1\n---\n\n# One\n\nOld paragraph.\n'
        old_md = tmp_path / "chapter.md"
        old_md.write_text(md)
        md_uri = await remote.upload(old_md, remote.key(2, "s3quality", "output/01_One/chapter.md"))
        manifest = {
            "chapters": [{"order": 1, "source_pages": [1, 1], "word_count": len(md.split())}]
        }
        initial_manifest = tmp_path / "chapters.json"
        initial_manifest.write_text(json.dumps(manifest))
        await remote.upload(initial_manifest, remote.key(2, "s3quality", "output/chapters.json"))
        original_zip = tmp_path / "book.zip"
        with zipfile.ZipFile(original_zip, "w") as archive:
            archive.writestr("01_One/chapter.md", md)
            archive.writestr("chapters.json", json.dumps(manifest))
        zip_uri = await remote.upload(
            original_zip, remote.key(2, "s3quality", "output/download.zip")
        )
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                User(id=2, username="owner", password_hash="hash", role="USER", status="ACTIVE")
            )
            session.add(
                Job(
                    id="s3quality",
                    owner_id=2,
                    filename="book.pdf",
                    stored_path=original_uri,
                    storage_backend="minio",
                    status=JobStatus.SUCCEEDED,
                    page_count=1,
                    output_path=zip_uri,
                    chapters=[
                        Chapter(
                            order_index=1,
                            title="One",
                            folder_name="01_One",
                            md_path=md_uri,
                            word_count=len(md.split()),
                        )
                    ],
                )
            )
            await session.commit()
            await SearchIndexService(session, local, remote).index_job("s3quality")
            assert (await search_library(session, 2, False, "Old paragraph", 10))["results"]
            service = QualityReviewService(session, local, remote)
            job = await session.get(Job, "s3quality")
            assert job is not None
            assert (await service.page_image(job, 1, 1)).startswith(b"\x89PNG")
            new = md.replace("Old paragraph.", "Corrected paragraph.")
            saved = await service.edit(job.id, 1, content_hash(md), new, actor_id=2)
            assert await remote.read_text(job.chapters[0].md_path) == new
            assert job.output_path != zip_uri and await remote.exists(job.output_path)
            assert await remote.exists(zip_uri)
            assert saved["hash"] == content_hash(new)
            assert not (await search_library(session, 2, False, "Old paragraph", 10))["results"]
            assert (await search_library(session, 2, False, "Corrected paragraph", 10))["results"]
            undo = await service.undo(job.id, 1, saved["hash"], actor_id=2)
            assert undo["hash"] == content_hash(md)
            assert await remote.read_text(job.chapters[0].md_path) == md
            assert (await search_library(session, 2, False, "Old paragraph", 10))["results"]
            # Direct cleanup needs neither source PDF nor source-page mapping.
            await remote.delete_prefix(remote.key(2, "s3quality", "upload") + "/")
            direct = md.replace("# One", "# Revised").replace(
                "Old paragraph.", "Revised text.\n\n```mermaid\nflowchart TD\nA --> B\n```"
            )
            result = await service.edit(
                job.id, 1, content_hash(md), direct, actor_id=2, direct=True
            )
            new_md = await remote.read_text(job.chapters[0].md_path)
            assert result["hash"] == content_hash(new_md)
            assert 'title: "Revised"' in new_md
            assert job.chapters[0].mermaid_count == 1
            assert (await search_library(session, 2, False, "Revised text", 10))["results"]
            with tempfile.TemporaryDirectory() as temp:
                dest = Path(temp) / "revision.zip"
                await remote.download(job.output_path, dest)
                with zipfile.ZipFile(dest) as revised_zip:
                    assert revised_zip.read("01_One/chapter.md").decode() == new_md
                    manifest = json.loads(revised_zip.read("chapters.json"))
                    assert manifest["chapters"][0]["title"] == "Revised"
            await service.undo(job.id, 1, result["hash"], actor_id=2, direct=True)
            assert job.chapters[0].title == "One"
            assert job.chapters[0].mermaid_count == 0
        await engine.dispose()
