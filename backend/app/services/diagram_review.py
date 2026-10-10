"""Admin review of diagram fallbacks with versioned Markdown/ZIP publication."""

import asyncio
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.storage.file_store import IFileStore
from app.db.models import DiagramReviewItem, Job, utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import ConflictError, NotFoundError, ProviderError, ValidationError
from app.services.mermaid_validation import validate_mermaid

Decision = Literal["ACCEPTED_IMAGE", "EDITED_MERMAID"]
_ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpe?g|webp|gif|bmp|tiff?|jpx|jp2)$", re.I)


def render_block(item: DiagramReviewItem, decision: Decision, code: str | None) -> str:
    image = f"../assets/{item.asset_name}"
    if decision == "EDITED_MERMAID" and code:
        content = f"```mermaid\n{code}\n```\n\n[Original diagram image]({image})"
    else:
        content = f"![Original diagram]({image})"
    return (
        f"<!-- pdf2md-review:{item.id}:start -->\n{content}\n<!-- pdf2md-review:{item.id}:end -->"
    )


def replace_block(markdown: str, item: DiagramReviewItem, replacement: str) -> str:
    # The server minted this hex ID during extraction. The escaped anchor is an exact
    # occurrence identity, so a reused asset never changes a second occurrence.
    start = f"<!-- pdf2md-review:{item.id}:start -->"
    end = f"<!-- pdf2md-review:{item.id}:end -->"
    if markdown.count(start) != 1 or markdown.count(end) != 1:
        raise ConflictError("The diagram anchor is missing or ambiguous; no files changed")
    first, last = markdown.index(start), markdown.index(end) + len(end)
    if first >= last:
        raise ConflictError("The diagram anchor is malformed; no files changed")
    return markdown[:first] + replacement + markdown[last:]


def _write_revision(
    source: Path,
    destination: Path,
    chapter_path: str,
    chapter_text: str,
    manifest: dict[str, object],
) -> None:
    with zipfile.ZipFile(source) as existing, zipfile.ZipFile(destination, "w") as updated:
        names = set(existing.namelist())
        if chapter_path not in names or "chapters.json" not in names:
            raise ConflictError("ZIP is missing the expected chapter or manifest")
        for member in existing.infolist():
            if member.filename == chapter_path:
                updated.writestr(member, chapter_text.encode("utf-8"))
            elif member.filename == "chapters.json":
                updated.writestr(member, json.dumps(manifest, indent=2).encode("utf-8"))
            else:
                with existing.open(member) as reader, updated.open(member, "w") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)


class DiagramReviewService:
    def __init__(self, session: AsyncSession, store: IFileStore) -> None:
        self._session = session
        self._store = store

    async def list_for_job(self, job_id: str) -> list[DiagramReviewItem]:
        if await self._session.get(Job, job_id) is None:
            raise NotFoundError("Job not found")
        result = await self._session.scalars(
            select(DiagramReviewItem)
            .where(DiagramReviewItem.job_id == job_id)
            .order_by(
                DiagramReviewItem.chapter_order,
                DiagramReviewItem.page_number,
                DiagramReviewItem.position_y,
                DiagramReviewItem.occurrence_index,
            )
        )
        return list(result.all())

    async def decide(
        self, job_id: str, item_id: str, decision: Decision, source_code: str | None = None
    ) -> DiagramReviewItem:
        # PostgreSQL holds this lock through ZIP construction and DB commit. Readers
        # continue using the old paths until the new revision is published.
        job = await self._session.scalar(
            select(Job)
            .options(selectinload(Job.chapters))
            .where(Job.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise NotFoundError("Job not found")
        if job.status != JobStatus.PARTIAL:
            raise ConflictError("Only completed partial jobs have fallback diagrams to review")
        item = await self._session.scalar(
            select(DiagramReviewItem).where(
                DiagramReviewItem.id == item_id, DiagramReviewItem.job_id == job_id
            )
        )
        if item is None:
            raise NotFoundError("Review item not found")
        if not _ASSET.fullmatch(item.asset_name):
            raise ConflictError("Stored diagram asset is invalid")
        asset_path = self._store.get_output_path(job_id, f"assets/{item.asset_name}")
        if not await self._store.exists(asset_path):
            raise ConflictError("Original diagram asset is missing")
        code = None
        if decision == "EDITED_MERMAID":
            try:
                code = validate_mermaid(source_code or "")
            except ProviderError as exc:
                raise ValidationError("Invalid or unsafe Mermaid code") from exc
        elif source_code:
            raise ValidationError("Mermaid code is only allowed when editing a diagram")
        chapter = next((c for c in job.chapters if c.order_index == item.chapter_order), None)
        if chapter is None or not chapter.md_path or not job.output_path:
            raise ConflictError("Chapter or ZIP is unavailable")
        if not await self._store.exists(chapter.md_path) or not await self._store.exists(
            job.output_path
        ):
            raise ConflictError("Chapter or ZIP is missing")
        if decision == "ACCEPTED_IMAGE" and item.decision != "EDITED_MERMAID":
            item.decision, item.mermaid_code, item.reviewed_at = decision, None, utcnow()
            await self._session.commit()
            return item
        original = await self._store.read_text(chapter.md_path)
        replacement = render_block(item, decision, code)
        updated_text = replace_block(original, item, replacement)
        # Accepting an untouched image only updates review metadata; the ZIP is already correct.
        if updated_text == original:
            item.decision, item.mermaid_code, item.reviewed_at = decision, code, utcnow()
            await self._session.commit()
            return item

        member = f"{chapter.folder_name}/chapter.md"
        source = Path(job.output_path)
        with zipfile.ZipFile(source) as old_zip:
            manifest = json.loads(old_zip.read("chapters.json"))
        entry = next(
            (
                entry
                for entry in manifest.get("chapters", [])
                if entry.get("order") == chapter.order_index
            ),
            None,
        )
        if entry is None:
            raise ConflictError("Chapter is absent from the ZIP manifest")
        count_delta = int(decision == "EDITED_MERMAID") - int(item.decision == "EDITED_MERMAID")
        entry["mermaid_count"] = chapter.mermaid_count + count_delta
        entry["word_count"] = len(updated_text.split())
        version = uuid.uuid4().hex
        revision_dir = Path(self._store.get_output_path(job_id, f".revisions/{version}"))
        new_chapter = self._store.get_output_path(job_id, f".revisions/{version}/{member}")
        new_zip = self._store.get_output_path(job_id, f".revisions/{version}/{job_id}.zip")
        try:
            await self._store.save_output_text(
                job_id, f".revisions/{version}/{member}", updated_text
            )
            await asyncio.to_thread(
                _write_revision, source, Path(new_zip), member, updated_text, manifest
            )
            chapter.md_path = new_chapter
            chapter.mermaid_count += count_delta
            chapter.word_count = len(updated_text.split())
            job.output_path = new_zip
            job.updated_at = utcnow()
            item.decision, item.mermaid_code, item.reviewed_at = decision, code, utcnow()
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            await asyncio.to_thread(shutil.rmtree, revision_dir, ignore_errors=True)
            raise
        return item
