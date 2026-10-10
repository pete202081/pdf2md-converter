"""Local filesystem storage adapter.

Stores uploaded PDFs and generated outputs under two roots. All path
construction goes through :func:`_resolve_under` to prevent path traversal.
A S3-compatible adapter (MinIO) can be added later behind the same interface.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles

from app.domain.exceptions import StorageError

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_job_id(job_id: str) -> None:
    if not _SAFE_JOB_ID.fullmatch(job_id):
        raise StorageError("Invalid job identifier")


def sanitize_filename(filename: str) -> str:
    """Make a filename safe for the filesystem (no traversal, no weird chars)."""
    name = _SAFE_FILENAME.sub("_", filename).strip("._")
    return name or "upload.pdf"


class IFileStore(ABC):
    """Storage port used by services and pipeline stages."""

    @abstractmethod
    async def save_upload(self, job_id: str, filename: str, chunks: AsyncIterator[bytes]) -> str:
        """Stream an upload to disk; returns the stored absolute path."""

    @abstractmethod
    def get_upload_path(self, job_id: str, filename: str) -> str:
        """Absolute path where an upload for ``job_id`` lives (or would live)."""

    @abstractmethod
    def get_output_root(self, job_id: str) -> str:
        """Absolute path of the output directory for ``job_id``."""

    @abstractmethod
    def get_output_path(self, job_id: str, relative_path: str) -> str:
        """Absolute path of an output file (output root + job + relative path)."""

    @abstractmethod
    async def save_output_bytes(self, job_id: str, relative_path: str, data: bytes) -> str:
        """Write a file under the job's output directory; returns the absolute path."""

    @abstractmethod
    async def save_output_text(self, job_id: str, relative_path: str, text: str) -> str:
        """Write a UTF-8 text file under the job's output directory."""

    @abstractmethod
    async def read_text(self, path: str) -> str:
        """Read a UTF-8 text file."""

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Whether a path exists."""

    @abstractmethod
    async def delete_job_files(self, job_id: str) -> None:
        """Remove all stored files (upload + outputs) for a job."""


class LocalFileStore(IFileStore):
    """Stores files on local disk (mounted Docker volumes in production)."""

    def __init__(self, upload_dir: str, output_dir: str) -> None:
        self._upload_root = Path(upload_dir)
        self._output_root = Path(output_dir)

    def _resolve_under(self, base: Path, *parts: str) -> Path:
        """Join ``parts`` under ``base`` and refuse anything that escapes it."""
        base_resolved = base.resolve()
        candidate = base_resolved.joinpath(*parts).resolve()
        if candidate != base_resolved and base_resolved not in candidate.parents:
            raise StorageError(
                "Path escapes the allowed directory", details={"path": str(candidate)}
            )
        return candidate

    async def save_upload(self, job_id: str, filename: str, chunks: AsyncIterator[bytes]) -> str:
        _validate_job_id(job_id)
        safe_name = sanitize_filename(filename)
        directory = self._resolve_under(self._upload_root, job_id)
        await asyncio.to_thread(directory.mkdir, 0o755, True, True)
        target = self._resolve_under(directory, safe_name)
        async with aiofiles.open(target, "wb") as handle:
            async for chunk in chunks:
                await handle.write(chunk)
        return str(target)

    def get_upload_path(self, job_id: str, filename: str) -> str:
        _validate_job_id(job_id)
        return str(self._resolve_under(self._upload_root, job_id, sanitize_filename(filename)))

    def get_output_root(self, job_id: str) -> str:
        _validate_job_id(job_id)
        return str(self._resolve_under(self._output_root, job_id))

    def get_output_path(self, job_id: str, relative_path: str) -> str:
        if not relative_path or Path(relative_path).is_absolute():
            raise StorageError("Output path must be relative to the job directory")
        job_root = Path(self.get_output_root(job_id))
        return str(self._resolve_under(job_root, relative_path))

    async def save_output_bytes(self, job_id: str, relative_path: str, data: bytes) -> str:
        target = Path(self.get_output_path(job_id, relative_path))
        await asyncio.to_thread(target.parent.mkdir, 0o755, True, True)
        async with aiofiles.open(target, "wb") as handle:
            await handle.write(data)
        return str(target)

    async def save_output_text(self, job_id: str, relative_path: str, text: str) -> str:
        return await self.save_output_bytes(job_id, relative_path, text.encode("utf-8"))

    async def read_text(self, path: str) -> str:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    async def exists(self, path: str) -> bool:
        return await asyncio.to_thread(Path(path).is_file)

    async def delete_job_files(self, job_id: str) -> None:
        _validate_job_id(job_id)
        for root in (self._upload_root, self._output_root):
            target = self._resolve_under(root, job_id)
            await asyncio.to_thread(shutil.rmtree, target, True)  # ignore_errors=True
        archive = self._resolve_under(self._output_root, f"{job_id}.zip")
        await asyncio.to_thread(archive.unlink, True)  # missing_ok=True
