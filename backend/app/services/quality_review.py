"""Private source-page review and copy-on-write chapter corrections."""

import asyncio
import hashlib
import json
import re
import shutil
import tempfile
import uuid
import zipfile
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any

import pymupdf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.storage.file_store import IFileStore
from app.adapters.storage.minio_store import MinioObjectStore
from app.db.models import Chapter, ChapterEdit, Job, PageQualityFlag, utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import (
    ConflictError,
    NotFoundError,
    ProviderError,
    StorageError,
    ValidationError,
)
from app.services.library_search import SearchIndexService
from app.services.mermaid_validation import validate_mermaid
from app.services.revision_archive import write_revision

_FINISHED = (JobStatus.SUCCEEDED, JobStatus.PARTIAL)
_ANCHOR = re.compile(
    r"<!-- pdf2md-review:([A-Za-z0-9_-]+):start -->(.*?)<!-- pdf2md-review:\1:end -->", re.S
)
_MARKER = re.compile(r"<!-- pdf2md-review:[^>]+-->")
_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.S)
_MERMAID = re.compile(r"(?:```|~~~)mermaid\s*\n.*?\n(?:```|~~~)", re.S | re.I)
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_ASSET_LINK = re.compile(
    r"^\.\./assets/[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpe?g|webp|gif|bmp|tiff?|jpx|jp2)$", re.I
)
_RAW_HTML = re.compile(r"<!--.*?-->|<\s*/?\s*[a-z][^>]*>|<![^>]*>|<\?[^>]*>", re.I | re.S)


def _raw_html_tokens(markdown: str) -> Counter[str]:
    """Count untrusted HTML already present in extracted Markdown.

    Extraction may have included markup before this editor existed. A revision
    may retain or remove those exact tokens, but cannot introduce new ones.
    Generated review/page-break comments are handled by their own validators.
    """
    body = _MARKER.sub("", markdown).replace("<!-- page break -->", "")
    counts: Counter[str] = Counter()
    position = 0
    while True:
        start = body.find("<!--", position)
        if start < 0:
            counts.update(_RAW_HTML.findall(body[position:]))
            break
        counts.update(_RAW_HTML.findall(body[position:start]))
        end = body.find("-->", start + 4)
        if end < 0:
            # One unclosed comment consumes the remainder; scan it once, not
            # quadratically for every `<!--` inside attacker-controlled text.
            counts["<!--"] += 1
            break
        counts[body[start : end + 3]] += 1
        position = end + 3
    return counts


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_edit(old: str, new: str, *, full_content: bool = False) -> None:
    if not new.strip() or len(new.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValidationError("Chapter text must be non-empty and at most 2 MiB")
    old_meta, new_meta = _FRONTMATTER.match(old), _FRONTMATTER.match(new)
    if (old_meta.group(0) if old_meta else None) != (new_meta.group(0) if new_meta else None):
        raise ValidationError("Generated chapter metadata cannot be edited")
    before, after = _ANCHOR.findall(old), _ANCHOR.findall(new)
    if (
        before != after
        or len(_MARKER.findall(old)) != 2 * len(before)
        or len(_MARKER.findall(new)) != 2 * len(after)
    ):
        raise ValidationError("Diagram review regions must remain unchanged; use Review diagrams")
    if full_content:
        # Only diagram bodies outside protected review anchors may change.
        fenced = _MERMAID.findall(new)
        if len(re.findall(r"(?:```|~~~)mermaid\b", new, re.I)) != len(fenced):
            raise ValidationError("Mermaid code fences must be complete")
        for fence in fenced:
            try:
                validate_mermaid("\n".join(fence.splitlines()[1:-1]))
            except ProviderError as exc:
                raise ValidationError("Invalid or unsafe Mermaid diagram") from exc
    elif _MERMAID.findall(old) != _MERMAID.findall(new):
        raise ValidationError("Mermaid diagrams must remain unchanged; use Review diagrams")
    # Existing OCR/extracted HTML must not make unrelated chapter cleanup fail.
    # Only tokens already in the original revision may remain (or be removed).
    if _raw_html_tokens(new) - _raw_html_tokens(old):
        raise ValidationError("New raw HTML is not allowed in chapter edits")
    for link in _LINK.findall(new):
        url = link.strip()
        if not (
            _ASSET_LINK.fullmatch(url)
            or url.startswith("#")
            or re.fullmatch(r"https?://[^\s<>]+", url)
        ):
            raise ValidationError("Use only this job's assets, page anchors or HTTP(S) links")


def chapter_title_from_content(markdown: str, fallback: str) -> str:
    """Use the first level-one heading, ignoring generated frontmatter."""
    body = _FRONTMATTER.sub("", markdown, count=1)
    # A heading inside a code fence is not the chapter title.
    body = re.sub(r"^(?:```|~~~)[^\n]*\n.*?^(?:```|~~~)\s*$", "", body, flags=re.M | re.S)
    match = re.search(r"^# (.+?)\s*$", body, re.M)
    title = match.group(1).strip() if match else fallback
    if not match:
        meta = _FRONTMATTER.match(markdown)
        stored = re.search(r"^title:\s*(.+)$", meta.group(0), re.M) if meta else None
        if stored:
            try:
                value = json.loads(stored.group(1))
                if isinstance(value, str):
                    title = value
            except ValueError:
                pass
    if not title or len(title) > 512 or any(c in title for c in "\r\n\x00"):
        raise ValidationError("Chapter heading must be at most 512 characters")
    return title


def synchronize_title(markdown: str, title: str) -> str:
    """Only the generated title scalar may change; leave page identity intact."""
    match = _FRONTMATTER.match(markdown)
    if not match:
        return markdown  # Some older jobs have no frontmatter.
    prefix = match.group(0)
    if not re.search(r"^title:.*$", prefix, re.M):
        return markdown
    revised = re.sub(
        r"^title:.*$", lambda _: f"title: {json.dumps(title)}", prefix, count=1, flags=re.M
    )
    return revised + markdown[len(prefix) :]


def render_page(path: str, page_number: int) -> bytes:
    """Bound dimensions before rasterization; only the requested PDF page is exposed."""
    try:
        with pymupdf.open(path) as pdf:
            if page_number < 1 or page_number > len(pdf):
                raise NotFoundError("Source page does not exist")
            page = pdf[page_number - 1]
            scale = min(2.0, 1400.0 / max(page.rect.width, page.rect.height, 1.0))
            if scale <= 0:
                raise ValidationError("PDF page has invalid dimensions")
            data = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
            if len(data) > 8 * 1024 * 1024:
                raise ValidationError("Rendered page exceeds the image size limit")
            return data
    except (NotFoundError, ValidationError):
        raise
    except Exception as exc:
        raise ValidationError("Could not render the source PDF page") from exc


class QualityReviewService:
    def __init__(
        self, session: AsyncSession, local: IFileStore, remote: MinioObjectStore | None
    ) -> None:
        self.session, self.local, self.remote = session, local, remote

    async def _text(self, job: Job, path: str) -> str:
        if job.storage_backend == "minio":
            if not self.remote:
                raise StorageError("MinIO storage is not configured")
            prefix = self.remote.key(job.owner_id, job.id, "output") + "/"
            if not self.remote.parse(path).startswith(prefix):
                raise NotFoundError("Chapter or manifest is unavailable")
            return await self.remote.read_text(path)
        root = Path(self.local.get_output_root(job.id)).resolve()
        if root not in Path(path).resolve().parents:
            raise NotFoundError("Chapter or manifest is unavailable")
        if not await self.local.exists(path):
            raise NotFoundError("Chapter or manifest is unavailable")
        return await self.local.read_text(path)

    async def _manifest(self, job: Job) -> dict[str, Any]:
        if job.storage_backend == "minio":
            if not self.remote:
                raise StorageError("MinIO storage is not configured")
            path = self.remote.uri(self.remote.key(job.owner_id, job.id, "output/chapters.json"))
            if not await self.remote.exists(path):
                raise NotFoundError("Chapter source-page mapping is unavailable")
        else:
            path = self.local.get_output_path(job.id, "chapters.json")
        try:
            manifest = json.loads(await self._text(job, path))
            if not isinstance(manifest, dict) or not isinstance(manifest.get("chapters"), list):
                raise ValueError("Invalid manifest")
            return manifest
        except (ValueError, TypeError) as exc:
            raise ConflictError("Chapter source-page mapping is invalid") from exc

    async def chapter_pages(self, job: Job, order: int) -> tuple[int, int]:
        manifest = await self._manifest(job)
        entry = next((e for e in manifest["chapters"] if e.get("order") == order), None)
        if not entry or not isinstance(entry.get("source_pages"), list):
            raise NotFoundError("Chapter source-page mapping is unavailable")
        pages = entry["source_pages"]
        if len(pages) != 2 or not all(isinstance(n, int) for n in pages):
            raise ConflictError("Chapter source-page mapping is invalid")
        first, last = pages
        if first < 1 or first > last or last > job.page_count:
            raise ConflictError("Chapter source-page mapping is invalid")
        return first, last

    async def summary(self, job: Job) -> dict[str, Any]:
        if job.status not in _FINISHED:
            raise ConflictError("Quality review is available when conversion completes")
        flags = list(
            (
                await self.session.scalars(
                    select(PageQualityFlag)
                    .where(PageQualityFlag.job_id == job.id)
                    .order_by(PageQualityFlag.page_number, PageQualityFlag.code)
                )
            ).all()
        )
        chapters: list[dict[str, Any]] = []
        try:
            manifest = await self._manifest(job)
            for entry in manifest["chapters"]:
                if isinstance(entry, dict) and isinstance(entry.get("order"), int):
                    chapters.append(
                        {"order": entry["order"], "source_pages": entry.get("source_pages")}
                    )
        except (NotFoundError, ConflictError):
            pass  # Legacy jobs may have no usable manifest; don't invent page mappings.
        return {
            "inventory_ready": job.quality_inventory_ready,
            "chapters": chapters,
            "flags": [
                {
                    "id": flag.id,
                    "page_number": flag.page_number,
                    "chapter_order": flag.chapter_order,
                    "code": flag.code,
                    "message": flag.message,
                    "reviewed_at": flag.reviewed_at.isoformat() if flag.reviewed_at else None,
                }
                for flag in flags
            ],
        }

    async def mark_page(self, job: Job, page: int, actor_id: int) -> dict[str, Any]:
        if job.status not in _FINISHED or not job.quality_inventory_ready:
            raise ConflictError("Re-upload this PDF to generate a quality warning inventory")
        job = await self._locked(job.id)
        flags = list(
            (
                await self.session.scalars(
                    select(PageQualityFlag).where(
                        PageQualityFlag.job_id == job.id, PageQualityFlag.page_number == page
                    )
                )
            ).all()
        )
        if not flags:
            raise NotFoundError("No warnings exist for this page")
        for flag in flags:
            flag.reviewed_at, flag.reviewed_by_id = utcnow(), actor_id
        await self.session.commit()
        return await self.summary(job)

    async def page_image(self, job: Job, order: int, page: int) -> bytes:
        if job.status not in _FINISHED:
            raise ConflictError("Source pages are available when conversion completes")
        first, last = await self.chapter_pages(job, order)
        if page < first or page > last:
            raise NotFoundError("Source page is outside this chapter")
        if job.storage_backend == "minio":
            if not self.remote:
                raise StorageError("MinIO storage is not configured")
            # The original filename was sanitized before storage; validate the URI prefix
            # rather than deriving a path from untrusted filename metadata.
            prefix = self.remote.key(job.owner_id, job.id, "upload") + "/"
            if not self.remote.parse(job.stored_path).startswith(prefix):
                raise NotFoundError("Source PDF is unavailable")
            with tempfile.TemporaryDirectory(prefix="pdf2md-page-") as directory:
                source = Path(directory) / "source.pdf"
                await self.remote.download(job.stored_path, source)
                return await asyncio.to_thread(render_page, str(source), page)
        expected_local = self.local.get_upload_path(job.id, job.filename)
        if str(Path(job.stored_path).resolve()) != str(
            Path(expected_local).resolve()
        ) or not await self.local.exists(expected_local):
            raise NotFoundError("Source PDF is unavailable")
        return await asyncio.to_thread(render_page, expected_local, page)

    async def _require_source(self, job: Job, order: int) -> None:
        """Older jobs may be edited only while the original PDF and page map survive."""
        await self.chapter_pages(job, order)
        if job.storage_backend == "minio":
            if not self.remote:
                raise StorageError("MinIO storage is not configured")
            prefix = self.remote.key(job.owner_id, job.id, "upload") + "/"
            if not self.remote.parse(job.stored_path).startswith(
                prefix
            ) or not await self.remote.exists(job.stored_path):
                raise NotFoundError("Source PDF is unavailable")
        else:
            expected = self.local.get_upload_path(job.id, job.filename)
            if Path(job.stored_path).resolve() != Path(
                expected
            ).resolve() or not await self.local.exists(expected):
                raise NotFoundError("Source PDF is unavailable")

    async def _locked(self, job_id: str) -> Job:
        job = await self.session.scalar(
            select(Job)
            .options(selectinload(Job.chapters))
            .where(Job.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise NotFoundError("Job not found")
        if job.status not in _FINISHED:
            raise ConflictError("Only completed jobs can be edited")
        return job

    async def _publish(
        self,
        job: Job,
        chapter: Chapter,
        updated: str,
        *,
        title: str | None = None,
        mermaid_count: int | None = None,
    ) -> tuple[str, str]:
        if not job.output_path:
            raise ConflictError("Download archive is unavailable")
        member = f"{chapter.folder_name}/chapter.md"
        version = uuid.uuid4().hex
        if job.storage_backend == "minio":
            if not self.remote:
                raise StorageError("MinIO storage is not configured")
            prefix = self.remote.key(job.owner_id, job.id, f"output/.revisions/{version}") + "/"
            try:
                with tempfile.TemporaryDirectory(prefix="pdf2md-edit-") as temp:
                    source, revised, md_file = (
                        Path(temp) / name for name in ("source.zip", "updated.zip", "chapter.md")
                    )
                    await self.remote.download(job.output_path, source)
                    manifest = await asyncio.to_thread(
                        self._updated_manifest, source, chapter, updated, title, mermaid_count
                    )
                    await asyncio.to_thread(
                        write_revision, source, revised, member, updated, manifest
                    )
                    await asyncio.to_thread(md_file.write_text, updated, encoding="utf-8")
                    md_uri = await self.remote.upload(md_file, prefix + member)
                    zip_uri = await self.remote.upload(revised, prefix + "download.zip")
                    return md_uri, zip_uri
            except Exception:
                with suppress(Exception):
                    await self.remote.delete_prefix(prefix)
                raise
        if not await self.local.exists(job.output_path):
            raise ConflictError("Download archive is missing")
        revision_dir = Path(self.local.get_output_path(job.id, f".revisions/{version}"))
        new_md = self.local.get_output_path(job.id, f".revisions/{version}/{member}")
        new_zip = self.local.get_output_path(job.id, f".revisions/{version}/{job.id}.zip")
        try:
            manifest = await asyncio.to_thread(
                self._updated_manifest,
                Path(job.output_path),
                chapter,
                updated,
                title,
                mermaid_count,
            )
            await self.local.save_output_text(job.id, f".revisions/{version}/{member}", updated)
            await asyncio.to_thread(
                write_revision, Path(job.output_path), Path(new_zip), member, updated, manifest
            )
            return new_md, new_zip
        except Exception:
            await asyncio.to_thread(shutil.rmtree, revision_dir, ignore_errors=True)
            raise

    @staticmethod
    def _updated_manifest(
        source: Path,
        chapter: Chapter,
        updated: str,
        title: str | None = None,
        mermaid_count: int | None = None,
    ) -> dict[str, Any]:
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            for link in _LINK.findall(updated):
                if _ASSET_LINK.fullmatch(link) and link[3:] not in names:
                    raise ValidationError("Referenced image is missing from this job's ZIP")
            manifest: dict[str, Any] = json.loads(archive.read("chapters.json"))
        entry = next(
            (e for e in manifest.get("chapters", []) if e.get("order") == chapter.order_index), None
        )
        if entry is None:
            raise ConflictError("Chapter is absent from the ZIP manifest")
        entry["word_count"] = len(updated.split())
        if title is not None:
            entry["title"] = title
        if mermaid_count is not None:
            entry["mermaid_count"] = mermaid_count
        return manifest

    async def _save(
        self, job: Job, chapter: Chapter, updated: str, *, direct: bool = False
    ) -> None:
        title = chapter_title_from_content(updated, chapter.title) if direct else None
        mermaid_count = len(_MERMAID.findall(updated)) if direct else None
        new_md, new_zip = await self._publish(
            job, chapter, updated, title=title, mermaid_count=mermaid_count
        )
        try:
            chapter.md_path, job.output_path = new_md, new_zip
            chapter.word_count = len(updated.split())
            if title is not None:
                chapter.title = title
            if mermaid_count is not None:
                chapter.mermaid_count = mermaid_count
            job.updated_at = utcnow()
            flags = (
                await self.session.scalars(
                    select(PageQualityFlag).where(
                        PageQualityFlag.job_id == job.id,
                        PageQualityFlag.chapter_order == chapter.order_index,
                    )
                )
            ).all()
            for flag in flags:
                flag.reviewed_at, flag.reviewed_by_id = None, None
            await SearchIndexService(self.session, self.local, self.remote).revision(
                job, chapter, updated
            )
        except Exception:
            await self._discard_revision(job.id, job.owner_id, job.storage_backend, new_zip)
            raise

    async def _discard_revision(
        self, job_id: str, owner_id: int, backend: str, path: str | None
    ) -> None:
        """Best-effort cleanup after a failed SQL commit; never touch the old ZIP."""
        if not path:
            return
        try:
            if backend == "minio":
                if self.remote:
                    key = self.remote.parse(path)
                    prefix = self.remote.key(owner_id, job_id, "output/.revisions") + "/"
                    if key.startswith(prefix):
                        await self.remote.delete_prefix(key.rsplit("/", 1)[0] + "/")
            else:
                revisions = Path(self.local.get_output_path(job_id, ".revisions")).resolve()
                archive = Path(path).resolve()
                if archive.parent.parent == revisions:
                    await asyncio.to_thread(shutil.rmtree, archive.parent, ignore_errors=True)
        except Exception:
            # Keep the committed prior revision safe if cleanup itself fails.
            pass

    async def edit(
        self,
        job_id: str,
        order: int,
        expected_hash: str,
        updated: str,
        actor_id: int,
        *,
        direct: bool = False,
    ) -> dict[str, str]:
        job = await self._locked(job_id)
        chapter = next((c for c in job.chapters if c.order_index == order), None)
        if chapter is None or not chapter.md_path:
            raise NotFoundError("Chapter is unavailable")
        if not direct:
            await self._require_source(job, order)
        original = await self._text(job, chapter.md_path)
        if content_hash(original) != expected_hash:
            raise ConflictError("Chapter changed while you were editing; reload before saving")
        validate_edit(original, updated, full_content=direct)
        if direct:
            updated = synchronize_title(updated, chapter_title_from_content(updated, chapter.title))
        if updated == original:
            return {"hash": expected_hash, "message": "No changes to save"}
        prior_path = chapter.md_path
        owner_id, backend = job.owner_id, job.storage_backend
        published: str | None = None
        try:
            await self._save(job, chapter, updated, direct=direct)
            published = job.output_path
            self.session.add(
                ChapterEdit(
                    job_id=job_id,
                    chapter_order=order,
                    actor_id=actor_id,
                    old_md_path=prior_path,
                    old_hash=expected_hash,
                    new_hash=content_hash(updated),
                )
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            await self._discard_revision(job_id, owner_id, backend, published)
            raise
        return {"hash": content_hash(updated), "message": "Chapter saved"}

    async def undo(
        self,
        job_id: str,
        order: int,
        expected_hash: str,
        actor_id: int,
        *,
        direct: bool = False,
    ) -> dict[str, str]:
        job = await self._locked(job_id)
        chapter = next((c for c in job.chapters if c.order_index == order), None)
        if chapter is None or not chapter.md_path:
            raise NotFoundError("Chapter is unavailable")
        if not direct:
            await self._require_source(job, order)
        current = await self._text(job, chapter.md_path)
        if content_hash(current) != expected_hash:
            raise ConflictError("Chapter changed; reload before undoing")
        edit = await self.session.scalar(
            select(ChapterEdit)
            .where(
                ChapterEdit.job_id == job_id,
                ChapterEdit.chapter_order == order,
                ChapterEdit.undone_at.is_(None),
            )
            .order_by(ChapterEdit.id.desc())
            .limit(1)
        )
        if not edit or edit.new_hash != expected_hash:
            raise ConflictError("Cannot undo: an intervening change exists or no edit is available")
        previous = await self._text(job, edit.old_md_path)
        owner_id, backend = job.owner_id, job.storage_backend
        published = None
        try:
            await self._save(job, chapter, previous, direct=direct)
            published = job.output_path
            edit.undone_at, edit.undone_by_id = utcnow(), actor_id
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            await self._discard_revision(job_id, owner_id, backend, published)
            raise
        return {"hash": content_hash(previous), "message": "Last chapter edit undone"}
