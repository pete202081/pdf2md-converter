"""GitHub adapter safety and private conversion integration (no live GitHub calls)."""

import hashlib
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
import fitz
import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.github_pdf import GitHubPDF, validate_path, validate_repo
from app.adapters.storage.file_store import LocalFileStore
from app.api.deps import get_container, get_session, get_upload_service
from app.api.v1.routers.auth import COOKIE, digest
from app.api.v1.routers.github import get_github
from app.config import Settings
from app.db.models import Base, User, UserSession, utcnow
from app.domain.exceptions import ConflictError, NotFoundError, ValidationError
from app.main import create_app
from app.repositories.job_repository import JobRepository
from app.services.upload_service import UploadService


def fixture() -> tuple[GitHubPDF, str, bytes, list[httpx.Request]]:
    pdf = fitz.open()
    pdf.new_page().insert_text((40, 40), "Short PDF")
    data = pdf.tobytes()
    sha = hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()
    revision = "b" * 40
    seen: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        public = {"full_name": "books/guide", "private": False, "default_branch": "main"}
        secret = {"full_name": "books/secret", "private": True, "default_branch": "main"}
        if "/search/repositories" in url:
            return httpx.Response(
                200, json={"items": [public, secret], "incomplete_results": False}
            )
        if "/repos/books/secret" in url and url.endswith("/secret"):
            return httpx.Response(200, json=secret)
        if url.endswith("/repos/books/guide"):
            return httpx.Response(200, json=public)
        if "/commits/main" in url:
            return httpx.Response(200, json={"sha": revision})
        if "/git/trees/" in url:
            return httpx.Response(
                200,
                json={
                    "tree": [{"path": "guide.pdf", "type": "blob", "sha": sha, "size": len(data)}],
                    "truncated": False,
                },
            )
        if "/user/repos" in url:
            return httpx.Response(200, json=[secret])
        if "/contents/guide.pdf" in url:
            if "raw" in request.headers.get("accept", ""):
                return httpx.Response(200, content=data)
            return httpx.Response(200, json={"type": "file", "sha": sha, "size": len(data)})
        if "/contents/" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "type": "file",
                        "name": "guide.pdf",
                        "path": "guide.pdf",
                        "sha": sha,
                        "size": len(data),
                    }
                ],
            )
        if "/user" in url:
            return httpx.Response(200, json={"login": "editor"})
        return httpx.Response(404)

    return GitHubPDF("token-secret", transport=httpx.MockTransport(response)), sha, data, seen


async def test_public_search_filters_private_and_browse_checks_role() -> None:
    adapter, sha, data, seen = fixture()
    result = await adapter.search("guide", 1)
    assert [item["repo"] for item in result["repositories"]] == ["books/guide"]
    assert result["repositories"][0]["files"][0]["sha"] == sha
    assert [item["repo"] for item in await adapter.private_repositories(1)] == ["books/secret"]
    with pytest.raises(NotFoundError):
        await adapter.browse("books/secret", "", admin=False)
    assert (await adapter.browse("books/guide", "", admin=False))["files"][0]["size"] == len(data)
    uploaded = await adapter.download("books/guide", "guide.pdf", "b" * 40, sha, 1000000)
    assert await uploaded.read() == data
    await uploaded.close()
    assert all("token-secret" not in str(request.url) for request in seen)
    with pytest.raises(ConflictError):
        await adapter.download("books/guide", "guide.pdf", "b" * 40, "a" * 40, 1000000)
    with pytest.raises(ValidationError):
        validate_path("../../private.pdf")
    with pytest.raises(ValidationError):
        validate_repo("https://example.com/ssrf")


async def test_github_conversion_private_job_and_access_controls(tmp_path: Path) -> None:
    adapter, sha, _, _ = fixture()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = fakeredis.aioredis.FakeRedis()
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    async with factory() as session:
        for user_id, role in ((1, "USER"), (2, "ADMIN")):
            session.add(
                User(
                    id=user_id,
                    username=f"person{user_id}",
                    role=role,
                    status="ACTIVE",
                    password_hash="hash",
                )
            )
            session.add(
                UserSession(
                    user_id=user_id,
                    token_hash=digest(f"token{user_id}"),
                    csrf_token=f"csrf{user_id}",
                    expires_at=utcnow() + timedelta(hours=2),
                )
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
    app.dependency_overrides[get_github] = lambda: adapter

    async def upload_factory():
        async with factory() as session:
            yield UploadService(store, JobRepository(session), Settings(), lambda job_id: None)

    app.dependency_overrides[get_upload_service] = upload_factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = {"repo": "books/guide", "path": "guide.pdf", "sha": sha, "revision": "b" * 40}
        assert (await client.get("/api/v1/github/search?query=guide")).status_code == 401
        client.cookies.set(COOKIE, "token1", path="/api/v1")
        assert (await client.get("/api/v1/github/private-repositories")).status_code == 403
        assert (
            await client.post(
                "/api/v1/github/browse",
                json={"repo": "books/secret"},
                headers={"X-CSRF-Token": "csrf1"},
            )
        ).status_code == 404
        private = {**body, "repo": "books/secret"}
        assert (
            await client.post(
                "/api/v1/github/convert", json=private, headers={"X-CSRF-Token": "csrf1"}
            )
        ).status_code == 404
        assert (await client.post("/api/v1/github/convert", json=body)).status_code == 403
        created = await client.post(
            "/api/v1/github/convert", json=body, headers={"X-CSRF-Token": "csrf1"}
        )
        assert created.status_code == 201, created.text
        value = created.json()
        assert value["source_type"] == "GITHUB" and value["source_repo"] == "books/guide"
        assert (
            Path(store.get_upload_path(value["id"], "guide.pdf")).read_bytes().startswith(b"%PDF-")
        )
        assert (await client.get("/api/v1/shelves/public")).json()["books"] == []
        client.cookies.set(COOKIE, "token2", path="/api/v1")
        private_list = await client.get("/api/v1/github/private-repositories")
        assert private_list.status_code == 200
        assert private_list.headers["Cache-Control"] == "private, no-store"
        assert (
            await client.post(
                "/api/v1/github/browse",
                json={"repo": "books/secret"},
                headers={"X-CSRF-Token": "csrf2"},
            )
        ).status_code in (
            200,
            404,
        )
    await redis.aclose()
    await engine.dispose()


async def test_download_rejects_redirect_to_untrusted_host_without_token() -> None:
    adapter, sha, _, _ = fixture()
    requests: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "raw" in request.headers.get("accept", ""):
            return httpx.Response(
                302, headers={"Location": "http://169.254.169.254/latest/meta-data"}
            )
        return httpx.Response(200, json={"type": "file", "sha": sha, "size": 200})

    adapter.transport = httpx.MockTransport(redirect)
    with pytest.raises(ValidationError, match="destination"):
        await adapter.download("books/guide", "guide.pdf", "b" * 40, sha, 1000000)
    assert len(requests) == 2
    assert all(request.url.host == "api.github.com" for request in requests)


async def test_binary_and_size_failures_are_private_safe() -> None:
    adapter, sha, _, _ = fixture()
    with pytest.raises(ValidationError, match="size limit"):
        await adapter.download("books/guide", "guide.pdf", "b" * 40, sha, 100)

    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:test\nsize 1\n"
    pointer_sha = hashlib.sha1(
        f"blob {len(pointer)}\0".encode() + pointer, usedforsecurity=False
    ).hexdigest()

    def lfs(request: httpx.Request) -> httpx.Response:
        if "raw" in request.headers.get("accept", ""):
            return httpx.Response(200, content=pointer)
        return httpx.Response(200, json={"type": "file", "sha": pointer_sha, "size": len(pointer)})

    adapter.transport = httpx.MockTransport(lfs)
    with pytest.raises(ValidationError, match="LFS pointers"):
        await adapter.download("books/guide", "guide.pdf", "b" * 40, pointer_sha, 1000000)


async def test_github_pdf_uses_existing_minio_upload_pipeline(tmp_path: Path) -> None:
    from moto import mock_aws

    from app.adapters.storage.minio_store import MinioObjectStore

    adapter, sha, data, _ = fixture()
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
            file = await adapter.download(
                "books/guide", "guide.pdf", "b" * 40, sha, 50 * 1024 * 1024
            )
            try:
                job = await UploadService(
                    store,
                    JobRepository(session),
                    Settings(storage_backend="minio"),
                    lambda job_id: None,
                    remote,
                ).accept_upload(file, owner_id=1, source=("books/guide", "guide.pdf", "b" * 40))
            finally:
                await file.close()
            assert job.stored_path.startswith("s3://books/1/")
            target = tmp_path / "roundtrip.pdf"
            await remote.download(job.stored_path, target)
            assert target.read_bytes() == data
            assert not Path(store.get_upload_path(job.id, "guide.pdf")).exists()
        await engine.dispose()


async def test_public_search_does_not_disclose_repo_that_became_private() -> None:
    seen: list[str] = []

    def changed(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/search/repositories":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"full_name": "hidden/ebook", "private": False, "default_branch": "main"}
                    ]
                },
            )
        if request.url.path == "/repos/hidden/ebook":
            return httpx.Response(
                200, json={"full_name": "hidden/ebook", "private": True, "default_branch": "main"}
            )
        raise AssertionError("Private tree must not be requested")

    gateway = GitHubPDF("server-pat", transport=httpx.MockTransport(changed))
    result = await gateway.search("ebook", 1)
    assert result["repositories"] == []
    assert "/repos/hidden/ebook/git/trees/" not in " ".join(seen)
