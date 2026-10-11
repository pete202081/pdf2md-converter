"""Immutable book snapshots, defensive Markdown imports and shelf storage."""

import asyncio
import json
import mimetypes
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiofiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.storage.file_store import IFileStore
from app.adapters.storage.minio_store import MinioObjectStore
from app.db.models import Job, ShelfAsset, ShelfBook, ShelfChapter
from app.domain.enums import JobStatus
from app.domain.exceptions import NotFoundError, StorageError, ValidationError
from app.logging import get_logger

log = get_logger("shelves")
_MAX_UPLOAD = 50 * 1024 * 1024
_MAX_TOTAL = 200 * 1024 * 1024  # uncompressed Markdown import cap
_MAX_CONVERSION_ZIP = 2 * 1024 * 1024 * 1024  # trusted output, before opening/copying
_MAX_CONVERSION_ENTRIES = 20_000  # central-directory entries, including unused files
_MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024  # selected chapters and referenced assets only
_MAX_SNAPSHOT_ASSETS = 5_000
_MAX_CHAPTER = 2 * 1024 * 1024
_MAX_ASSET = 8 * 1024 * 1024
_SAFE_FILE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}$")
_ASSET_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_REMOTE_IMAGE = re.compile(r"(?i)(?:!\[[^\]]*\]\(\s*(?:https?:|data:|//)|<\s*img\b)")
_REFERENCE_IMAGE = re.compile(r"!\[[^\]]*\](?:\s*\[[^\]]*\])?")
_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.S)
_REVIEW_MARKER = re.compile(r"<!-- pdf2md-review:[A-Za-z0-9_-]+:(?:start|end) -->")


@dataclass
class PreparedShelf:
    title: str
    author: str | None
    description: str | None
    chapters: list[tuple[str, str]]  # (display title, relative file path)
    assets: list[tuple[str, str]]  # (basename, media type)


def _label(value: Any, maximum: int, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum:
        raise ValidationError("Book metadata is missing or too long")
    # Do not allow NUL/control characters in DB fields or public cards.
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValidationError("Book metadata contains unsupported characters")
    return value.strip() or None


def _archive(archive: zipfile.ZipFile, *, imported: bool) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if imported:
        # Keep the original, stricter bounds for user-supplied ZIP uploads.
        if len(infos) > 800 or sum(i.file_size for i in infos) > _MAX_TOTAL:
            raise ValidationError("Book archive exceeds shelf limits")
    elif len(infos) > _MAX_CONVERSION_ENTRIES:
        raise ValidationError("Conversion ZIP contains too many files for a shelf snapshot")
    names: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
            or stat.S_ISLNK(info.external_attr >> 16)
        ):
            raise ValidationError("Archive contains an unsafe path or link")
        if info.is_dir():
            continue
        if name.casefold() in names or info.flag_bits & 1:
            raise ValidationError("Archive contains a duplicate or encrypted file")
        if imported and info.file_size > 1024 and info.compress_size < info.file_size / 250:
            raise ValidationError("Archive compression ratio exceeds safety limit")
        names[name.casefold()] = info
    return names


def _member(
    archive: zipfile.ZipFile, names: dict[str, zipfile.ZipInfo], path: str, limit: int
) -> bytes:
    entry = names.get(path.casefold())
    if entry is None or entry.filename != path or entry.file_size > limit:
        raise ValidationError("Book is missing a required file or file exceeds limits")
    data = archive.read(entry)
    if len(data) > limit:
        raise ValidationError("Book file exceeds limits")
    return data


def _markdown(text: str, names: dict[str, zipfile.ZipInfo], target: Path) -> list[tuple[str, str]]:
    if _REMOTE_IMAGE.search(text):
        raise ValidationError("Remote or HTML images are not allowed")
    if _REFERENCE_IMAGE.search(_IMAGE.sub("", text)):
        raise ValidationError("Use inline images with a book-local assets/ path")
    # The original conversion remains untouched. The shelf copy must not reveal
    # review IDs, generated PDF page metadata, or invalid PostgreSQL NUL bytes.
    text = _REVIEW_MARKER.sub("", _FRONTMATTER.sub("", text, count=1)).replace("\x00", " ")
    assets: dict[str, str] = {}

    def image(match: re.Match[str]) -> str:
        raw = match.group(2).strip()
        if raw.startswith("../assets/"):
            name = raw[len("../assets/") :]
        elif raw.startswith("assets/"):
            name = raw[len("assets/") :]
        else:
            raise ValidationError("Use only book-local images under assets/")
        if not _SAFE_FILE.fullmatch(name) or Path(name).suffix.lower() not in _ASSET_EXT:
            raise ValidationError("Unsupported book image reference")
        if f"assets/{name}".casefold() not in names:
            raise ValidationError("Referenced book image is missing")
        assets[name] = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return f"![{match.group(1)}](../assets/{name})"

    text = _IMAGE.sub(image, text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return list(assets.items())


def prepare_archive(
    path: Path, directory: Path, *, imported: bool, single_title: str | None = None
) -> PreparedShelf:
    """Validate and extract only declared chapters and their safe raster assets."""
    if not zipfile.is_zipfile(path):
        if not imported or path.suffix.lower() != ".md" or path.stat().st_size > _MAX_CHAPTER:
            raise ValidationError("Upload one Markdown file or a book ZIP")
        title = _label(single_title or path.stem, 200, required=True)
        assert title
        text = path.read_text(encoding="utf-8")
        if _IMAGE.search(text) or _REMOTE_IMAGE.search(text):
            raise ValidationError("Use a book ZIP for Markdown containing images")
        _markdown(text, {}, directory / "chapters/1.md")
        return PreparedShelf(title, None, None, [(title, "chapters/1.md")], [])
    try:
        with zipfile.ZipFile(path) as archive:
            names = _archive(archive, imported=imported)
            if imported:
                data = _member(archive, names, "book.json", 32 * 1024)
                manifest = json.loads(data.decode("utf-8"))
                if not isinstance(manifest, dict):
                    raise ValidationError("book.json must be an object")
                title = _label(manifest.get("title"), 200, required=True)
                author = _label(manifest.get("author"), 120)
                description = _label(manifest.get("description"), 1000)
                entries = manifest.get("chapters")
            else:
                raise ValidationError("Conversion snapshots use the dedicated snapshot method")
            if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
                raise ValidationError("book.json requires 1 to 100 ordered chapters")
            chapters: list[tuple[str, str]] = []
            images: dict[str, str] = {}
            used: set[str] = set()
            for order, item in enumerate(entries, start=1):
                if not isinstance(item, dict):
                    raise ValidationError("Invalid chapter entry")
                chapter_title = _label(item.get("title"), 200, required=True)
                source = item.get("file")
                if (
                    not isinstance(source, str)
                    or not source.startswith("chapters/")
                    or not _SAFE_FILE.fullmatch(source[len("chapters/") :])
                    or not source.lower().endswith(".md")
                    or source.casefold() in used
                ):
                    raise ValidationError("Chapters must name unique files under chapters/")
                used.add(source.casefold())
                raw = _member(archive, names, source, _MAX_CHAPTER).decode("utf-8")
                target = f"chapters/{order}.md"
                images.update(_markdown(raw, names, directory / target))
                assert chapter_title
                chapters.append((chapter_title, target))
            for name in images:
                (directory / "assets").mkdir(exist_ok=True)
                (directory / "assets" / name).write_bytes(
                    _member(archive, names, f"assets/{name}", _MAX_ASSET)
                )
            assert title
            return PreparedShelf(title, author, description, chapters, list(images.items()))
    except (UnicodeDecodeError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValidationError("Book ZIP or Markdown is invalid or not UTF-8") from exc


def prepare_conversion(path: Path, job: Job, directory: Path) -> PreparedShelf:
    try:
        with zipfile.ZipFile(path) as archive:
            names = _archive(archive, imported=False)
            chapters: list[tuple[str, str]] = []
            images: dict[str, str] = {}
            selected_bytes = 0
            if not 1 <= len(job.chapters) <= 100:
                raise ValidationError("Conversion has no chapters or exceeds shelf limits")
            for order, chapter in enumerate(sorted(job.chapters, key=lambda c: c.order_index), 1):
                source = f"{chapter.folder_name}/chapter.md"
                if not _SAFE_FILE.fullmatch(chapter.folder_name):
                    raise ValidationError("Converted chapter folder is invalid")
                info = names.get(source.casefold())
                if info is not None:
                    selected_bytes += info.file_size
                if selected_bytes > _MAX_SNAPSHOT_BYTES:
                    raise ValidationError(
                        "Referenced book content exceeds the shelf snapshot limit"
                    )
                raw = _member(archive, names, source, _MAX_CHAPTER).decode("utf-8")
                target = f"chapters/{order}.md"
                images.update(_markdown(raw, names, directory / target))
                if len(images) > _MAX_SNAPSHOT_ASSETS:
                    raise ValidationError(
                        "Conversion references too many images for a shelf snapshot"
                    )
                title = _label(chapter.title, 200, required=True)
                assert title
                chapters.append((title, target))
            for name in images:
                asset_path = f"assets/{name}"
                info = names.get(asset_path.casefold())
                if info is not None:
                    selected_bytes += info.file_size
                if selected_bytes > _MAX_SNAPSHOT_BYTES:
                    raise ValidationError(
                        "Referenced book content exceeds the shelf snapshot limit"
                    )
                data = _member(archive, names, asset_path, _MAX_ASSET)
                (directory / "assets").mkdir(exist_ok=True)
                (directory / "assets" / name).write_bytes(data)
            return PreparedShelf(
                Path(job.filename).stem[:200], None, None, chapters, list(images.items())
            )
    except (UnicodeDecodeError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValidationError("Conversion archive is invalid or not UTF-8") from exc


class ShelfStorage:
    def __init__(self, local: IFileStore, remote: MinioObjectStore | None):
        self.local, self.remote = local, remote

    def _root(self, book_id: str) -> Path:
        return Path(self.local.get_output_root("shelves")) / book_id

    async def publish(self, directory: Path, book: ShelfBook) -> None:
        if book.storage_backend == "minio":
            if not self.remote:
                raise StorageError("MinIO storage is not configured")
            prefix = self.remote.key(book.owner_id, book.id, "shelf") + "/"
            try:
                for file in sorted(directory.rglob("*")):
                    if file.is_file():
                        await self.remote.upload(
                            file, prefix + file.relative_to(directory).as_posix()
                        )
            except Exception:
                await self.remote.delete_prefix(prefix)
                raise
        else:
            root = self._root(book.id)
            staging = root.parent / (".staging-" + book.id)
            await asyncio.to_thread(root.parent.mkdir, parents=True, exist_ok=True)
            try:
                await asyncio.to_thread(shutil.copytree, directory, staging)
                await asyncio.to_thread(staging.rename, root)
            finally:
                await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)

    async def remove(self, book: ShelfBook) -> None:
        if book.storage_backend == "minio":
            if not self.remote:
                raise StorageError("MinIO storage is not configured")
            await self.remote.delete_prefix(self.remote.key(book.owner_id, book.id, "shelf") + "/")
        else:
            await asyncio.to_thread(shutil.rmtree, self._root(book.id), ignore_errors=True)

    async def read(self, book: ShelfBook, relative: str) -> bytes:
        if book.storage_backend == "minio":
            if not self.remote:
                raise StorageError("MinIO storage is not configured")
            uri = self.remote.uri(self.remote.key(book.owner_id, book.id, "shelf/" + relative))
            chunks = await self.remote.stream(uri)
            data = bytearray()
            try:
                for chunk in chunks:
                    data.extend(chunk)
                    if len(data) > _MAX_ASSET:
                        raise StorageError("Shelf file exceeds limit")
                return bytes(data)
            finally:
                close = getattr(chunks, "close", None)
                if close:
                    close()
        path = (self._root(book.id) / relative).resolve()
        if self._root(book.id).resolve() not in path.parents:
            raise StorageError("Shelf path is invalid")
        if (await asyncio.to_thread(path.stat)).st_size > _MAX_ASSET:
            raise StorageError("Shelf file exceeds limit")
        raw = await asyncio.to_thread(path.read_bytes)
        if len(raw) > _MAX_ASSET:
            raise StorageError("Shelf file exceeds limit")
        return raw


class ShelfPublisher:
    def __init__(self, session: AsyncSession, storage: ShelfStorage):
        self.session, self.storage = session, storage

    async def _save(
        self, owner_id: int, backend: str, kind: str, prepared: PreparedShelf, directory: Path
    ) -> ShelfBook:
        book = ShelfBook(
            id=uuid.uuid4().hex,
            owner_id=owner_id,
            title=prepared.title,
            author=prepared.author,
            description=prepared.description,
            storage_backend=backend,
            source_kind=kind,
            visibility="PRIVATE",
        )
        book.chapters = [
            ShelfChapter(order_index=i, title=title, relative_path=relative)
            for i, (title, relative) in enumerate(prepared.chapters, 1)
        ]
        book.assets = [ShelfAsset(name=name, media_type=mime) for name, mime in prepared.assets]
        try:
            await self.storage.publish(directory, book)
            self.session.add(book)
            await self.session.commit()
            log.info(
                "shelf_created", book_id=book.id, owner_id=owner_id, chapters=len(book.chapters)
            )
            return book
        except Exception:
            await self.session.rollback()
            try:
                await self.storage.remove(book)
            except Exception:
                log.warning("shelf_cleanup_failed", book_id=book.id)
            raise

    async def from_job(self, job_id: str, owner_id: int) -> ShelfBook:
        job = await self.session.scalar(
            select(Job)
            .options(selectinload(Job.chapters))
            .where(Job.id == job_id, Job.owner_id == owner_id)
            .with_for_update()
        )
        if (
            not job
            or job.status not in (JobStatus.SUCCEEDED, JobStatus.PARTIAL)
            or not job.output_path
        ):
            raise NotFoundError("Completed conversion not found")
        with tempfile.TemporaryDirectory(prefix="pdf2md-shelf-") as temp:
            archive = Path(temp) / "source.zip"
            if job.storage_backend == "minio":
                if not self.storage.remote:
                    raise StorageError("MinIO storage is not configured")
                if await self.storage.remote.size(job.output_path) > _MAX_CONVERSION_ZIP:
                    raise ValidationError("Conversion ZIP exceeds the 2 GiB shelf snapshot limit")
                await self.storage.remote.download(job.output_path, archive)
            else:
                source = Path(job.output_path).resolve()
                output_root = Path(self.storage.local.get_output_root(job_id)).resolve()
                initial_zip = output_root.parent / f"{job_id}.zip"
                if source != initial_zip and output_root not in source.parents:
                    raise NotFoundError("Conversion ZIP is unavailable")
                try:
                    source_bytes = (await asyncio.to_thread(source.stat)).st_size
                except FileNotFoundError as exc:
                    raise NotFoundError("Conversion ZIP is unavailable") from exc
                if source_bytes > _MAX_CONVERSION_ZIP:
                    raise ValidationError("Conversion ZIP exceeds the 2 GiB shelf snapshot limit")
                await asyncio.to_thread(shutil.copyfile, source, archive)
            if archive.stat().st_size > _MAX_CONVERSION_ZIP:
                raise ValidationError("Conversion ZIP exceeds the 2 GiB shelf snapshot limit")
            prepared = await asyncio.to_thread(
                prepare_conversion, archive, job, Path(temp) / "shelf"
            )
            return await self._save(
                owner_id, job.storage_backend, "CONVERSION_SNAPSHOT", prepared, Path(temp) / "shelf"
            )

    async def import_file(
        self, file: Any, owner_id: int, backend: str, title: str | None = None
    ) -> ShelfBook:
        name = file.filename or "book.md"
        if not name.lower().endswith((".md", ".zip")):
            raise ValidationError("Upload Markdown or a book ZIP")
        with tempfile.TemporaryDirectory(prefix="pdf2md-import-") as temp:
            incoming = Path(temp) / (
                "incoming.md" if name.lower().endswith(".md") else "incoming.zip"
            )
            size = 0
            async with aiofiles.open(incoming, "wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > _MAX_UPLOAD:
                        raise ValidationError("Book upload exceeds 50 MiB")
                    await output.write(chunk)
            prepared = await asyncio.to_thread(
                prepare_archive,
                incoming,
                Path(temp) / "shelf",
                imported=True,
                single_title=title or Path(name).stem,
            )
            return await self._save(
                owner_id, backend, "MARKDOWN_IMPORT", prepared, Path(temp) / "shelf"
            )
