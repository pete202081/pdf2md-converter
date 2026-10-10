"""Private, current-revision chapter indexing and bounded SQL-side search.

The job row lock serializes a backfill with copy-on-write chapter revisions.
Search visibility changes in the same transaction as the current chapter path.
"""

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.storage.file_store import IFileStore
from app.adapters.storage.minio_store import MinioObjectStore
from app.db.models import Chapter, Job, SearchDocument, utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import StorageError
from app.logging import get_logger
from app.repositories.search_repository import SearchRepository

log = get_logger("library_search")
FINISHED = (JobStatus.SUCCEEDED, JobStatus.PARTIAL)
MAX_CONTENT = 2 * 1024 * 1024
MAX_MARKDOWN_BYTES = 4 * 1024 * 1024
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_PAGES = re.compile(r"^source_pages:\s*(\d+)-(\d+)\s*$", re.M)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_MERMAID = re.compile(r"(?:```|~~~)mermaid\s*\n.*?\n(?:```|~~~)", re.S | re.I)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_SPACES = re.compile(r"\s+")


def searchable_text(markdown: str) -> str:
    """Keep prose and tables, discard structural Markdown and hidden markers."""
    text = _FRONTMATTER.sub("", markdown, count=1)
    text = _COMMENT.sub(" ", text)
    text = _MERMAID.sub(" ", text)
    text = _LINK.sub(lambda match: match.group(1), text)
    text = re.sub(r"[|#*_`~>]", " ", text)
    # PostgreSQL text/tsvector reject NUL bytes. PDFs can embed them in otherwise
    # valid Markdown; remove control codes from the SEARCH COPY only, never the
    # source chapter, revision hash, or downloadable ZIP.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return _SPACES.sub(" ", text).strip()[:MAX_CONTENT]


def source_range(markdown: str) -> tuple[int | None, int | None]:
    match = _FRONTMATTER.match(markdown)
    if match:
        pages = _PAGES.search(match.group(1))
        if pages:
            first, last = int(pages.group(1)), int(pages.group(2))
            if 1 <= first <= last <= 1_000_000:
                return first, last
    return None, None


def make_document(job: Job, chapter: Chapter, markdown: str) -> SearchDocument:
    first, last = source_range(markdown)
    if job.page_count and last is not None and last > job.page_count:
        first, last = None, None  # Invalid provenance is not a usable PDF page range.
    # Filename and title are indexed even when the chapter has no extracted body.
    content = f"{job.filename} {chapter.title} {searchable_text(markdown)}"
    return SearchDocument(
        job_id=job.id,
        chapter_order=chapter.order_index,
        book_filename=job.filename,
        chapter_title=chapter.title,
        content=content,
        content_hash=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        first_page=first,
        last_page=last,
        indexed_at=utcnow(),
    )


class SearchIndexService:
    def __init__(
        self, session: AsyncSession, local: IFileStore | None, remote: MinioObjectStore | None
    ):
        self.session, self.local, self.remote = session, local, remote

    async def read_current(self, job: Job, chapter: Chapter) -> str:
        if not chapter.md_path:
            raise StorageError("Current chapter has no Markdown")
        if job.storage_backend == "minio":
            if not self.remote:
                raise StorageError("MinIO storage is not configured")
            key = self.remote.parse(chapter.md_path)
            prefix = self.remote.key(job.owner_id, job.id, "output") + "/"
            if not key.startswith(prefix):
                raise StorageError("Chapter URI is outside this book")
            return await self.remote.read_text_limited(chapter.md_path, MAX_MARKDOWN_BYTES)
        if self.local is None:
            raise StorageError("Local storage is not configured")
        root = Path(self.local.get_output_root(job.id)).resolve()
        if root not in Path(chapter.md_path).resolve().parents:
            raise StorageError("Chapter path is outside this book")
        if (await asyncio.to_thread(Path(chapter.md_path).stat)).st_size > MAX_MARKDOWN_BYTES:
            raise StorageError("Chapter exceeds search indexing size limit")
        return await self.local.read_text(chapter.md_path)

    async def index_job(self, job_id: str) -> int:
        """Replace a whole book atomically; safe to retry after interruption."""
        job = await self.session.scalar(
            select(Job)
            .options(selectinload(Job.chapters))
            .where(Job.id == job_id)
            .with_for_update()
        )
        if job is None or job.status not in FINISHED:
            return 0
        # Read before modifying rows: a storage failure cannot leave a half index.
        docs = [
            make_document(job, ch, await self.read_current(job, ch))
            for ch in sorted(job.chapters, key=lambda c: c.order_index)
        ]
        await self.session.execute(delete(SearchDocument).where(SearchDocument.job_id == job.id))
        self.session.add_all(docs)
        job.search_index_state = "READY"
        await self.session.commit()
        log.info("search_index_ready", job_id=job.id, chapters=len(docs))
        return len(docs)

    async def invalidate(self, job_id: str) -> None:
        """After an indexing error, hide all stale hits without harming the ZIP."""
        await self.session.execute(delete(SearchDocument).where(SearchDocument.job_id == job_id))
        job = await self.session.get(Job, job_id)
        if job is not None:
            job.search_index_state = "ERROR"
        await self.session.commit()
        log.warning("search_index_unavailable", job_id=job_id)

    async def revision(self, job: Job, chapter: Chapter, markdown: str) -> None:
        """Call inside the revision transaction, before its commit and under its job lock.

        A failed index update must not block an otherwise downloadable revision.
        The savepoint confines a SQL error; invalidation then hides all old results.
        """
        try:
            async with self.session.begin_nested():
                document = make_document(job, chapter, markdown)
                old = await self.session.get(SearchDocument, (job.id, chapter.order_index))
                if old is None:
                    self.session.add(document)
                else:
                    for field in (
                        "book_filename",
                        "chapter_title",
                        "content",
                        "content_hash",
                        "first_page",
                        "last_page",
                        "indexed_at",
                    ):
                        setattr(old, field, getattr(document, field))
                await self.session.flush()
        except Exception:
            # DB errors are already rolled back to the savepoint.
            log.warning("search_revision_failed", job_id=job.id)
            await self.session.execute(
                delete(SearchDocument).where(SearchDocument.job_id == job.id)
            )
            job.search_index_state = "ERROR"


def excerpt(content: str, query: str) -> str:
    # Plain text only; the React client never interprets it as markup.
    terms = re.findall(r"\w+", query.casefold())
    lowered = content.casefold()
    positions = [lowered.find(term) for term in terms if len(term) >= 2]
    position = min((p for p in positions if p >= 0), default=0)
    start = max(0, position - 55)
    end = min(len(content), start + 190)
    return ("…" if start else "") + content[start:end] + ("…" if end < len(content) else "")


async def search_library(
    session: AsyncSession, user_id: int, is_admin: bool, query: str, limit: int
) -> dict[str, Any]:
    """Present only bounded, plain-text excerpts from SQL-authorized rows."""
    docs, pending = await SearchRepository(session).find(user_id, is_admin, query, limit)
    return {
        "results": [
            {
                "job_id": doc.job_id,
                "filename": doc.book_filename,
                "chapter_order": doc.chapter_order,
                "chapter_title": doc.chapter_title,
                "first_page": doc.first_page,
                "last_page": doc.last_page,
                "excerpt": excerpt(doc.content, query),
            }
            for doc in docs
        ],
        "pending_books": pending,
    }
