"""Publish completed worker-local output to private MinIO before job becomes downloadable."""

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.storage.file_store import IFileStore
from app.adapters.storage.minio_store import MinioObjectStore
from app.db.models import Job
from app.domain.exceptions import ProcessingError
from app.domain.results import PipelineContext
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService


class PublishObjectsStage(BasePipelineStage):
    name = "publish_objects"

    def __init__(
        self,
        progress: ProgressService,
        local: IFileStore,
        remote: MinioObjectStore,
        session: AsyncSession,
    ) -> None:
        super().__init__(progress)
        self._local = local
        self._remote = remote
        self._session = session

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        job = await self._session.scalar(
            select(Job).options(selectinload(Job.chapters)).where(Job.id == ctx.job_id)
        )
        if job is None or job.storage_backend != "minio":
            raise ProcessingError("Object publication requires a MinIO job")
        root = Path(self._local.get_output_root(ctx.job_id))
        zip_path = Path(str(ctx.data["output_path"]))
        prefix = self._remote.key(job.owner_id, job.id, "output") + "/"
        published: dict[str, str] = {}
        try:
            files = await asyncio.to_thread(
                lambda: sorted(p for p in root.rglob("*") if p.is_file())
            )
            for path in files:
                await ctx.ensure_active()
                relative = path.relative_to(root).as_posix()
                published[relative] = await self._remote.upload(path, prefix + relative)
            await ctx.ensure_active()
            zip_uri = await self._remote.upload(zip_path, prefix + "download.zip")
            for chapter in job.chapters:
                if chapter.md_path:
                    relative = Path(chapter.md_path).relative_to(root).as_posix()
                    chapter.md_path = published[relative]
            await self._session.commit()
            ctx.data["output_path"] = zip_uri
            return {"objects": len(published) + 1}
        except Exception:
            await self._session.rollback()
            try:
                await self._remote.delete_prefix(prefix)
            except Exception:
                self._logger.exception("partial_object_cleanup_failed", job_id=job.id)
            raise
