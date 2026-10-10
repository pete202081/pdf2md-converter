"""Tests for the local filesystem storage adapter."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.adapters.storage.file_store import LocalFileStore, sanitize_filename
from app.domain.exceptions import StorageError


def test_sanitize_filename_replaces_unsafe_characters() -> None:
    assert sanitize_filename("My Book (final).pdf") == "My_Book__final_.pdf"
    assert sanitize_filename("plain.pdf") == "plain.pdf"


def test_sanitize_filename_blocks_traversal() -> None:
    assert sanitize_filename("../../etc/passwd") == "etc_passwd"
    assert sanitize_filename("..\\..\\win.pdf") == "win.pdf"
    assert sanitize_filename("...") == "upload.pdf"
    assert sanitize_filename("") == "upload.pdf"


async def test_save_upload_streams_chunks_to_disk(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))

    async def chunks() -> AsyncIterator[bytes]:
        yield b"%PDF-"
        yield b"1234"

    stored = await store.save_upload("job-1", "My Book.pdf", chunks())

    assert Path(stored).read_bytes() == b"%PDF-1234"
    assert Path(stored).parent == (tmp_path / "uploads" / "job-1").resolve()


async def test_get_output_root_stays_under_output_dir(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    root = Path(store.get_output_root("job-9"))
    assert root == (tmp_path / "outputs" / "job-9").resolve()


async def test_path_traversal_is_blocked(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    with pytest.raises(StorageError):
        store.get_output_root("../escape")


async def test_read_text_and_exists(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    target = tmp_path / "uploads" / "note.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Hello", encoding="utf-8")

    assert await store.exists(str(target)) is True
    assert await store.read_text(str(target)) == "# Hello"
    assert await store.exists(str(tmp_path / "missing.md")) is False


async def test_delete_job_files_removes_upload_and_output_dirs(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    upload_dir = tmp_path / "uploads" / "job-1"
    output_dir = tmp_path / "outputs" / "job-1"
    upload_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (upload_dir / "book.pdf").write_bytes(b"%PDF-")
    (output_dir / "01_chapter").mkdir()

    await store.delete_job_files("job-1")

    assert not upload_dir.exists()
    assert not output_dir.exists()


async def test_save_output_text_and_bytes_create_parent_dirs(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))

    md_path = await store.save_output_text("job-9", "01_Intro/chapter.md", "# Hello")
    img_path = await store.save_output_bytes("job-9", "assets/pic.png", b"\x89PNG")

    assert Path(md_path).read_text(encoding="utf-8") == "# Hello"
    assert Path(img_path).read_bytes() == b"\x89PNG"
    assert Path(md_path).parent == (tmp_path / "outputs" / "job-9" / "01_Intro").resolve()


async def test_get_output_path_blocks_traversal(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    with pytest.raises(StorageError):
        store.get_output_path("job-1", "../escape.txt")
    with pytest.raises(StorageError):
        store.get_output_path("../job-2", "chapter.md")


async def test_delete_job_files_removes_zip_and_rejects_invalid_id(tmp_path: Path) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    archive = output_root / "job-1.zip"
    archive.write_bytes(b"PK")
    await store.delete_job_files("job-1")
    assert not archive.exists()
    for job_id in ("", ".", "..", "../../outside", "/"):
        with pytest.raises(StorageError):
            await store.delete_job_files(job_id)
