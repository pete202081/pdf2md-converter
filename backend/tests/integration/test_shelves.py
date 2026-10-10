"""Private/public shelf lifecycle, import safety and separate reader progress."""

import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.adapters.storage.minio_store import MinioObjectStore
from app.api.deps import get_container, get_session
from app.api.v1.routers.auth import COOKIE, digest
from app.config import Settings
from app.db.models import Base, Chapter, Job, User, UserSession, utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import ValidationError
from app.main import create_app
from app.services.shelves import ShelfPublisher, ShelfStorage, prepare_archive


def sample_zip(*, image: bool = True) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "book.json",
            json.dumps(
                {
                    "title": "A Green Garden",
                    "author": "Owner",
                    "chapters": [
                        {"title": "First", "file": "chapters/first.md"},
                        {"title": "Second", "file": "chapters/second.md"},
                    ],
                }
            ),
        )
        archive.writestr(
            "chapters/first.md",
            "# First\nA rose blooms.\n" + ("![Rose](../assets/rose.png)" if image else ""),
        )
        archive.writestr("chapters/second.md", "# Second\nThe last page.")
        if image:
            archive.writestr("assets/rose.png", b"\x89PNG\r\n\x1a\nrose")
    return data.getvalue()


async def test_private_public_import_and_per_user_progress(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    redis = fakeredis.aioredis.FakeRedis()
    async with factory() as session:
        for user_id in (1, 2):
            session.add(
                User(
                    id=user_id,
                    username=f"reader{user_id}",
                    role="USER",
                    status="ACTIVE",
                    password_hash="hash",
                )
            )
            session.add(
                UserSession(
                    user_id=user_id,
                    token_hash=digest(f"token{user_id}"),
                    csrf_token=f"csrf{user_id}",
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
        session.add(
            User(
                id=3, username="administrator", role="ADMIN", status="ACTIVE", password_hash="hash"
            )
        )
        session.add(
            UserSession(
                user_id=3,
                token_hash=digest("token3"),
                csrf_token="csrf3",
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        session.add_all(
            [
                Job(
                    id="owned-job",
                    filename="my-book.pdf",
                    owner_id=1,
                    stored_path="unused",
                    status=JobStatus.SUCCEEDED,
                ),
                Job(
                    id="other-job",
                    filename="other-book.pdf",
                    owner_id=2,
                    stored_path="unused",
                    status=JobStatus.SUCCEEDED,
                ),
            ]
        )
        await session.commit()
    app = create_app()
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(
        file_store=store, object_store=None, settings=Settings(), redis=redis
    )

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        data = sample_zip()
        assert (await client.get("/api/v1/shelves/public")).json()["books"] == []
        assert (
            await client.post("/api/v1/shelves/import", files={"file": ("book.zip", data)})
        ).status_code == 401
        client.cookies.set(COOKIE, "token1", path="/api/v1")
        assert [
            item["id"] for item in (await client.get("/api/v1/shelves/eligible-jobs")).json()
        ] == ["owned-job"]
        assert (
            await client.post("/api/v1/shelves/import", files={"file": ("book.zip", data)})
        ).status_code == 403
        created = await client.post(
            "/api/v1/shelves/import",
            headers={"X-CSRF-Token": "csrf1"},
            files={"file": ("book.zip", data)},
        )
        assert created.status_code == 201, created.text
        book = created.json()
        book_id, first, second = book["id"], book["chapters"][0]["id"], book["chapters"][1]["id"]
        assert book["visibility"] == "PRIVATE"
        public = f"/api/v1/shelves/public/{book_id}"
        mine = f"/api/v1/shelves/mine/{book_id}"
        assert (await client.get(public)).status_code == 404
        assert (await client.get(f"{mine}/chapters/{first}")).status_code == 200
        client.cookies.set(COOKIE, "token2", path="/api/v1")
        assert (await client.get(mine)).status_code == 404
        assert (await client.get(f"/api/v1/shelves/reading/{book_id}")).status_code == 404
        client.cookies.set(COOKIE, "token3", path="/api/v1")
        assert (await client.get(f"/api/v1/shelves/admin/books/{book_id}")).status_code == 200
        assert (
            await client.get(f"/api/v1/shelves/admin/books/{book_id}/chapters/{first}")
        ).status_code == 200
        assert (
            await client.patch(
                mine, json={"visibility": "PUBLIC"}, headers={"X-CSRF-Token": "csrf3"}
            )
        ).status_code == 404
        client.cookies.set(COOKIE, "token1", path="/api/v1")
        assert (await client.patch(mine, json={"visibility": "PUBLIC"})).status_code == 403
        assert (
            await client.patch(
                mine, json={"visibility": "PUBLIC"}, headers={"X-CSRF-Token": "csrf1"}
            )
        ).status_code == 200
        client.cookies.clear()
        listing = await client.get("/api/v1/shelves/public")
        assert [item["id"] for item in listing.json()["books"]] == [book_id]
        assert (await client.get(public)).json()["chapters"][0]["title"] == "First"
        chapter_response = await client.get(f"{public}/chapters/{first}")
        assert (
            "rose.png" in chapter_response.text
            and chapter_response.headers["cache-control"] == "no-store"
        )
        asset = await client.get(f"{public}/assets/rose.png")
        assert asset.status_code == 200 and asset.content.startswith(b"\x89PNG")
        assert (
            await client.put(f"/api/v1/shelves/reading/{book_id}", json={"chapter_id": first})
        ).status_code == 401
        client.cookies.set(COOKIE, "token2", path="/api/v1")
        progress = await client.put(
            f"/api/v1/shelves/reading/{book_id}",
            json={"chapter_id": first, "completed": True},
            headers={"X-CSRF-Token": "csrf2"},
        )
        assert progress.status_code == 200, progress.text
        assert progress.json()["completed_chapter_ids"] == [first]
        assert progress.json()["resume_chapter_id"] == second
        assert (
            await client.put(
                f"/api/v1/shelves/reading/{book_id}",
                json={"chapter_id": second, "completed": True},
                headers={"X-CSRF-Token": "csrf2"},
            )
        ).json()["all_complete"]
        # Another user's completion is independent and can be undone.
        progress = await client.put(
            f"/api/v1/shelves/reading/{book_id}",
            json={"chapter_id": first, "completed": False},
            headers={"X-CSRF-Token": "csrf2"},
        )
        assert progress.json()["completed_chapter_ids"] == [second]
        client.cookies.set(COOKIE, "token1", path="/api/v1")
        owner_progress = (await client.get(f"/api/v1/shelves/reading/{book_id}")).json()
        assert owner_progress["completed_chapter_ids"] == []
        assert owner_progress["resume_chapter_id"] == first
        assert (
            await client.patch(
                mine, json={"visibility": "PRIVATE"}, headers={"X-CSRF-Token": "csrf1"}
            )
        ).status_code == 200
        client.cookies.clear()
        assert (await client.get(public)).status_code == 404
        assert (await client.get(f"{public}/assets/rose.png")).status_code == 404
        client.cookies.set(COOKIE, "token2", path="/api/v1")
        assert (await client.get(f"/api/v1/shelves/reading/{book_id}")).status_code == 404
        client.cookies.set(COOKIE, "token1", path="/api/v1")
        assert (
            await client.patch(
                mine, json={"visibility": "PUBLIC"}, headers={"X-CSRF-Token": "csrf1"}
            )
        ).status_code == 200
        client.cookies.set(COOKIE, "token2", path="/api/v1")
        assert (await client.get(f"/api/v1/shelves/reading/{book_id}")).json()[
            "completed_chapter_ids"
        ] == [second]
        assert (
            await client.delete(
                f"/api/v1/shelves/reading/{book_id}", headers={"X-CSRF-Token": "csrf2"}
            )
        ).status_code == 204
        assert (await client.get(f"/api/v1/shelves/reading/{book_id}")).json()[
            "completed_chapter_ids"
        ] == []
        client.cookies.set(COOKIE, "token1", path="/api/v1")
        assert (await client.delete(mine, headers={"X-CSRF-Token": "csrf1"})).status_code == 204
        assert (await client.get(mine)).status_code == 404
    await redis.aclose()
    await engine.dispose()


async def test_converted_snapshot_survives_job_changes_and_deletion(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    archive = tmp_path / "outputs" / "source.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "01_One/chapter.md",
            "---\nsource_pages: 1-2\n---\n# One\n"
            "<!-- pdf2md-review:abc:start -->Original chapter!"
            "<!-- pdf2md-review:abc:end -->",
        )
    async with factory() as session:
        session.add(
            User(id=1, username="owner", role="USER", status="ACTIVE", password_hash="hash")
        )
        session.add(
            Job(
                id="source",
                filename="Source.pdf",
                owner_id=1,
                stored_path="unused",
                output_path=str(archive),
                status=JobStatus.SUCCEEDED,
                chapters=[
                    Chapter(order_index=1, title="One", folder_name="01_One", md_path="unused")
                ],
            )
        )
        await session.commit()
        publisher = ShelfPublisher(session, ShelfStorage(store, None))
        shelf = await publisher.from_job("source", 1)
        assert shelf.visibility == "PRIVATE"
        chapter_id = shelf.chapters[0].id
        archive.unlink()
        await session.delete(await session.get(Job, "source"))
        await session.commit()
        content = await ShelfStorage(store, None).read(shelf, "chapters/1.md")
        assert b"# One" in content and b"Original chapter!" in content and chapter_id
        assert b"source_pages" not in content and b"pdf2md-review" not in content
    await engine.dispose()


def test_import_archive_rejects_traversal_external_images_and_bombs(tmp_path: Path) -> None:
    folder = tmp_path / "out"
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../stolen.md", "bad")
        archive.writestr("book.json", "{}")
    with pytest.raises(ValidationError, match="unsafe"):
        prepare_archive(zip_path, folder, imported=True)
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "book.json",
            json.dumps({"title": "Bad", "chapters": [{"title": "One", "file": "chapters/a.md"}]}),
        )
        archive.writestr("chapters/a.md", "![Tracking](https://example.org/pixel.png)")
    with pytest.raises(ValidationError, match="Remote"):
        prepare_archive(zip_path, folder, imported=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "book.json",
            json.dumps({"title": "Bad", "chapters": [{"title": "One", "file": "chapters/a.md"}]}),
        )
        archive.writestr("chapters/a.md", "A" * 20000)
    with pytest.raises(ValidationError, match="compression ratio"):
        prepare_archive(zip_path, folder, imported=True)


async def test_minio_shelf_import_and_private_read(tmp_path: Path) -> None:
    with mock_aws():
        remote = MinioObjectStore("http://s3.amazonaws.com", "u", "s", "books")
        remote.client.create_bucket(Bucket="books")
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
        async with factory() as session:
            session.add(
                User(id=1, username="owner", role="USER", status="ACTIVE", password_hash="hash")
            )
            await session.commit()
            publisher = ShelfPublisher(session, ShelfStorage(store, remote))
            book = await publisher.import_file(
                UploadFile(filename="book.zip", file=io.BytesIO(sample_zip())), 1, "minio"
            )
            assert book.visibility == "PRIVATE" and book.storage_backend == "minio"
            assert b"rose" in await ShelfStorage(store, remote).read(book, "assets/rose.png")
        await engine.dispose()


async def test_minio_converted_book_snapshot_survives_source_deletion(tmp_path: Path) -> None:
    with mock_aws():
        remote = MinioObjectStore("http://s3.amazonaws.com", "u", "s", "books")
        remote.client.create_bucket(Bucket="books")
        archive = tmp_path / "conversion.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("01_One/chapter.md", "# One\n![Rose](../assets/rose.png)")
            output.writestr("assets/rose.png", b"\x89PNG\r\n\x1a\nrose")
        old_uri = await remote.upload(archive, remote.key(1, "source", "output/download.zip"))
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
        async with factory() as session:
            session.add(
                User(id=1, username="owner", role="USER", status="ACTIVE", password_hash="hash")
            )
            session.add(
                Job(
                    id="source",
                    filename="Source.pdf",
                    owner_id=1,
                    stored_path="unused",
                    output_path=old_uri,
                    storage_backend="minio",
                    status=JobStatus.SUCCEEDED,
                    chapters=[
                        Chapter(order_index=1, title="One", folder_name="01_One", md_path="unused")
                    ],
                )
            )
            await session.commit()
            shelf = await ShelfPublisher(session, ShelfStorage(store, remote)).from_job("source", 1)
            await remote.delete_prefix(remote.key(1, "source", "output") + "/")
            assert b"Rose" in await ShelfStorage(store, remote).read(shelf, "chapters/1.md")
            assert b"rose" in await ShelfStorage(store, remote).read(shelf, "assets/rose.png")
        await engine.dispose()


async def test_single_markdown_book_import_and_rejected_reference_image(tmp_path: Path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    async with factory() as session:
        session.add(
            User(id=1, username="owner", role="USER", status="ACTIVE", password_hash="hash")
        )
        await session.commit()
        book = await ShelfPublisher(session, ShelfStorage(store, None)).import_file(
            UploadFile(filename="my-story.md", file=io.BytesIO(b"# Start\nA short chapter.")),
            1,
            "local",
            "My Story",
        )
        assert book.title == "My Story" and book.visibility == "PRIVATE"
        assert len(book.chapters) == 1
        assert b"short chapter" in await ShelfStorage(store, None).read(book, "chapters/1.md")
    zip_path = tmp_path / "refs.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "book.json",
            json.dumps(
                {"title": "Unsafe", "chapters": [{"title": "One", "file": "chapters/a.md"}]}
            ),
        )
        archive.writestr(
            "chapters/a.md", "![Track] [image]\n[image]: https://example.org/pixel.png"
        )
    with pytest.raises(ValidationError, match="inline images"):
        prepare_archive(zip_path, tmp_path / "output", imported=True)
    await engine.dispose()


async def test_failed_publish_commit_removes_snapshot_files(tmp_path: Path) -> None:
    from unittest.mock import patch

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    async with factory() as session:
        session.add(
            User(id=1, username="owner", role="USER", status="ACTIVE", password_hash="hash")
        )
        await session.commit()
        with (
            patch.object(session, "commit", side_effect=RuntimeError("db offline")),
            pytest.raises(RuntimeError, match="db offline"),
        ):
            await ShelfPublisher(session, ShelfStorage(store, None)).import_file(
                UploadFile(filename="novel.md", file=io.BytesIO(b"# Story\nA page")),
                1,
                "local",
            )
        root = Path(store.get_output_root("shelves"))
        assert not root.exists() or not list(root.iterdir())
    await engine.dispose()
