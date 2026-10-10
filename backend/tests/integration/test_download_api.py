"""HTTP-level download contract (ZIP, error envelope, cleanup)."""

import zipfile
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.adapters.storage.file_store import LocalFileStore
from app.api.deps import get_container, get_file_store, get_job_repository
from app.api.v1.routers.auth import require_user
from app.db.models import Job, User
from app.domain.enums import JobStatus
from app.main import create_app
from tests.fakes import InMemoryJobRepository


async def test_download_zip_and_delete_cleans_archive(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    repository = InMemoryJobRepository()
    archive = tmp_path / "outputs" / "job-1.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("01_Intro/chapter.md", "# Intro")
    job = Job(
        id="job-1",
        filename="book.pdf",
        stored_path="unused.pdf",
        status=JobStatus.SUCCEEDED,
        output_path=str(archive),
    )
    await repository.create(job)
    app = create_app()
    app.dependency_overrides[require_user] = lambda: User(
        id=1, username="admin", role="ADMIN", status="ACTIVE"
    )
    app.dependency_overrides[get_container] = lambda: type(
        "ContainerFake", (), {"object_store": None}
    )()
    app.dependency_overrides[get_job_repository] = lambda: repository
    app.dependency_overrides[get_file_store] = lambda: store

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/jobs/job-1/download")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert 'filename="book_markdown.zip"' in response.headers["content-disposition"]
        assert response.content == archive.read_bytes()

        deleted = await client.delete("/api/v1/jobs/job-1")
        assert deleted.status_code == 204
        assert not archive.exists()

        missing = await client.get("/api/v1/jobs/job-1/download")
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "NOT_FOUND"


async def test_download_rejects_unfinished_and_missing_archive(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    repository = InMemoryJobRepository()
    job = Job(
        id="job-2",
        filename="book.pdf",
        stored_path="unused.pdf",
        status=JobStatus.RUNNING,
    )
    await repository.create(job)
    app = create_app()
    app.dependency_overrides[require_user] = lambda: User(
        id=1, username="admin", role="ADMIN", status="ACTIVE"
    )
    app.dependency_overrides[get_container] = lambda: type(
        "ContainerFake", (), {"object_store": None}
    )()
    app.dependency_overrides[get_job_repository] = lambda: repository
    app.dependency_overrides[get_file_store] = lambda: store

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pending = await client.get("/api/v1/jobs/job-2/download")
        assert pending.status_code == 409
        assert pending.json()["error_code"] == "CONFLICT"

        job.status = JobStatus.SUCCEEDED
        job.output_path = str(tmp_path / "outputs" / "missing.zip")
        missing = await client.get("/api/v1/jobs/job-2/download")
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "NOT_FOUND"


async def test_asset_route_serves_only_images_from_its_job(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    repository = InMemoryJobRepository()
    await repository.create(
        Job(id="job-img", filename="book.pdf", stored_path="unused.pdf", status=JobStatus.SUCCEEDED)
    )
    await store.save_output_bytes("job-img", "assets/img_p1_x42.png", b"\x89PNG\r\n\x1a\n")
    await store.save_output_text("job-img", "chapters.json", "private metadata")
    app = create_app()
    app.dependency_overrides[require_user] = lambda: User(
        id=1, username="admin", role="ADMIN", status="ACTIVE"
    )
    app.dependency_overrides[get_container] = lambda: type(
        "ContainerFake", (), {"object_store": None}
    )()
    app.dependency_overrides[get_job_repository] = lambda: repository
    app.dependency_overrides[get_file_store] = lambda: store

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        image = await client.get("/api/v1/jobs/job-img/assets/img_p1_x42.png")
        assert image.status_code == 200
        assert image.content == b"\x89PNG\r\n\x1a\n"
        assert image.headers["content-type"] == "image/png"
        assert image.headers["x-content-type-options"] == "nosniff"

        for name in ("chapters.json", "bad..png", "other.pdf", "missing.png"):
            response = await client.get(f"/api/v1/jobs/job-img/assets/{name}")
            assert response.status_code == 404
        other = await client.get("/api/v1/jobs/nonexistent/assets/img_p1_x42.png")
        assert other.status_code == 404
