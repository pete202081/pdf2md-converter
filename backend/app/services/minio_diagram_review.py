"""Copy-on-write diagram revisions for private object-backed jobs."""

import asyncio
import json
import tempfile
import uuid
import zipfile
from contextlib import suppress
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.storage.minio_store import MinioObjectStore
from app.db.models import DiagramReviewItem, Job, utcnow
from app.domain.enums import JobStatus
from app.domain.exceptions import ConflictError, NotFoundError, ProviderError, ValidationError
from app.services.diagram_review import (
    _ASSET,
    Decision,
    _write_revision,
    render_block,
    replace_block,
)
from app.services.mermaid_validation import validate_mermaid


class MinioDiagramReviewService:
    def __init__(self, session: AsyncSession, store: MinioObjectStore) -> None:
        self.session, self.store = session, store

    async def decide(
        self, job_id: str, item_id: str, decision: Decision, source_code: str | None = None
    ) -> DiagramReviewItem:
        job = await self.session.scalar(
            select(Job)
            .options(selectinload(Job.chapters))
            .where(Job.id == job_id)
            .with_for_update()
        )
        if job is None or job.storage_backend != "minio":
            raise NotFoundError("Job not found")
        if job.status != JobStatus.PARTIAL:
            raise ConflictError("Only completed partial jobs have fallback diagrams to review")
        item = await self.session.scalar(
            select(DiagramReviewItem).where(
                DiagramReviewItem.id == item_id, DiagramReviewItem.job_id == job_id
            )
        )
        if item is None:
            raise NotFoundError("Review item not found")
        if not _ASSET.fullmatch(item.asset_name):
            raise ConflictError("Stored diagram asset is invalid")
        asset = self.store.uri(
            self.store.key(job.owner_id, job.id, f"output/assets/{item.asset_name}")
        )
        if not await self.store.exists(asset):
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
        if not chapter or not chapter.md_path or not job.output_path:
            raise ConflictError("Chapter or ZIP is unavailable")
        if decision == "ACCEPTED_IMAGE" and item.decision != "EDITED_MERMAID":
            item.decision, item.mermaid_code, item.reviewed_at = decision, None, utcnow()
            await self.session.commit()
            return item
        original = await self.store.read_text(chapter.md_path)
        updated = replace_block(original, item, render_block(item, decision, code))
        if updated == original:
            item.decision, item.mermaid_code, item.reviewed_at = decision, code, utcnow()
            await self.session.commit()
            return item
        version = uuid.uuid4().hex
        member = f"{chapter.folder_name}/chapter.md"
        prefix = self.store.key(job.owner_id, job.id, f"output/.revisions/{version}") + "/"
        try:
            with tempfile.TemporaryDirectory(prefix="pdf2md-review-") as temp:
                source = Path(temp) / "source.zip"
                revised = Path(temp) / "updated.zip"
                revised_md = Path(temp) / "chapter.md"
                await self.store.download(job.output_path, source)
                with zipfile.ZipFile(source) as archive:
                    manifest = json.loads(archive.read("chapters.json"))
                entry = next(
                    (
                        e
                        for e in manifest.get("chapters", [])
                        if e.get("order") == chapter.order_index
                    ),
                    None,
                )
                if entry is None:
                    raise ConflictError("Chapter is absent from the ZIP manifest")
                delta = int(decision == "EDITED_MERMAID") - int(item.decision == "EDITED_MERMAID")
                entry["mermaid_count"] = chapter.mermaid_count + delta
                entry["word_count"] = len(updated.split())
                await asyncio.to_thread(_write_revision, source, revised, member, updated, manifest)
                await asyncio.to_thread(revised_md.write_text, updated, encoding="utf-8")
                new_md = await self.store.upload(revised_md, prefix + member)
                new_zip = await self.store.upload(revised, prefix + "download.zip")
                chapter.md_path = new_md
                chapter.mermaid_count += delta
                chapter.word_count = len(updated.split())
                job.output_path = new_zip
                job.updated_at = utcnow()
                item.decision, item.mermaid_code, item.reviewed_at = decision, code, utcnow()
                await self.session.commit()
                return item
        except Exception:
            await self.session.rollback()
            # Orphaned private objects are preferable to destroying the old revision.
            with suppress(Exception):
                await self.store.delete_prefix(prefix)
            raise
