"""Composition root (Dependency Inversion wiring).

Builds the object graph: settings → database/redis/file-store → repositories →
services → pipeline. Both the FastAPI app (per-request) and the Celery worker
build their dependencies from this single place, so there is exactly one wiring
location in the codebase.
"""

from dataclasses import dataclass

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ocr.tesseract_engine import TesseractOcrEngine
from app.adapters.storage.file_store import IFileStore, LocalFileStore
from app.adapters.storage.minio_store import MinioObjectStore
from app.adapters.vlm.factory import build_vlm_provider
from app.config import Settings, get_settings
from app.db.session import Database
from app.repositories.base import (
    IChapterRepository,
    IDiagramReviewRepository,
    IJobRepository,
    IProgressRepository,
    IStageRepository,
)
from app.repositories.chapter_repository import ChapterRepository
from app.repositories.diagram_review_repository import DiagramReviewRepository
from app.repositories.job_repository import JobRepository
from app.repositories.progress_repository import ProgressRepository
from app.repositories.stage_repository import StageRepository
from app.services.image_extractor import ImageExtractor
from app.services.pipeline.orchestrator import ConversionOrchestrator
from app.services.pipeline.stages.assemble_chapters import AssembleChaptersStage
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.pipeline.stages.classify_diagrams import ClassifyDiagramsStage
from app.services.pipeline.stages.convert_mermaid import ConvertMermaidStage
from app.services.pipeline.stages.detect_chapters import DetectChaptersStage
from app.services.pipeline.stages.extract_images import ExtractImagesStage
from app.services.pipeline.stages.extract_tables import ExtractTablesStage
from app.services.pipeline.stages.extract_text import ExtractTextStage
from app.services.pipeline.stages.package_output import PackageOutputStage
from app.services.pipeline.stages.publish_objects import PublishObjectsStage
from app.services.pipeline.stages.quality_hints import QualityHintsStage
from app.services.pipeline.stages.validate_upload import ValidateUploadStage
from app.services.progress_service import ProgressService
from app.services.text_extractor import TextExtractor
from app.services.upload_service import UploadService
from app.worker.celery_app import celery_app

CONVERSION_TASK_NAME = "conversion.run"


@dataclass(slots=True)
class Container:
    """Holds shared infrastructure and exposes factories for the rest."""

    settings: Settings
    database: Database
    redis: aioredis.Redis
    file_store: IFileStore
    object_store: MinioObjectStore | None = None

    # --- repositories (per-session) -------------------------------------
    def job_repository(self, session: AsyncSession) -> IJobRepository:
        return JobRepository(session)

    def stage_repository(self, session: AsyncSession) -> IStageRepository:
        return StageRepository(session)

    def progress_repository(self, session: AsyncSession) -> IProgressRepository:
        return ProgressRepository(session)

    def chapter_repository(self, session: AsyncSession) -> IChapterRepository:
        return ChapterRepository(session)

    def diagram_review_repository(self, session: AsyncSession) -> IDiagramReviewRepository:
        return DiagramReviewRepository(session)

    # --- services --------------------------------------------------------
    def progress_service(self, session: AsyncSession) -> ProgressService:
        return ProgressService(self.progress_repository(session), self.redis)

    def upload_service(
        self, session: AsyncSession, settings: Settings | None = None
    ) -> UploadService:
        return UploadService(
            file_store=self.file_store,
            job_repository=self.job_repository(session),
            settings=settings or self.settings,
            enqueue=lambda job_id: celery_app.send_task(CONVERSION_TASK_NAME, args=[job_id]),
            object_store=self.object_store,
        )

    def build_orchestrator(
        self, session: AsyncSession, upload_path_override: str | None = None
    ) -> ConversionOrchestrator:
        """Assemble the conversion pipeline.

        Stage order matters: detection first, then the extraction stages all
        append to the shared block list, then assembly writes the chapter
        files, and packaging zips the result. The diagram classification +
        Mermaid conversion stages run between extraction and assembly.
        """
        progress = self.progress_service(session)

        async def provider_failure(name: str) -> None:
            await self.redis.hincrby("pdf2md:metrics:provider_failure", name, 1)  # type: ignore[misc]

        stages: list[BasePipelineStage] = [
            ValidateUploadStage(progress),
            DetectChaptersStage(progress),
            ExtractTextStage(progress, TextExtractor(TesseractOcrEngine())),
            ExtractTablesStage(progress),
            ExtractImagesStage(progress, ImageExtractor(self.file_store)),
            ClassifyDiagramsStage(progress, self.file_store),
            ConvertMermaidStage(
                progress,
                self.file_store,
                build_vlm_provider(self.settings, on_failure=provider_failure),
            ),
            AssembleChaptersStage(
                progress,
                self.file_store,
                self.chapter_repository(session),
                review_repository=self.diagram_review_repository(session),
            ),
            QualityHintsStage(progress, session),
            PackageOutputStage(progress),
        ]
        if self.object_store and upload_path_override:
            stages.append(
                PublishObjectsStage(progress, self.file_store, self.object_store, session)
            )
        return ConversionOrchestrator(
            stages=stages,
            job_repository=self.job_repository(session),
            stage_repository=self.stage_repository(session),
            file_store=self.file_store,
            settings=self.settings,
            metrics_redis=self.redis,
            upload_path_override=upload_path_override,
        )

    async def close(self) -> None:
        await self.redis.aclose()
        await self.database.close()


def build_container(settings: Settings | None = None) -> Container:
    """Create a fully wired container."""
    settings = settings or get_settings()
    return Container(
        settings=settings,
        database=Database(settings.database_url),
        redis=aioredis.from_url(settings.redis_url, decode_responses=True),
        file_store=LocalFileStore(settings.upload_dir, settings.output_dir),
        object_store=(
            MinioObjectStore(
                settings.minio_endpoint,
                settings.minio_access_key,
                settings.minio_secret_key,
                settings.minio_bucket,
            )
            if settings.minio_endpoint
            else None
        ),
    )
