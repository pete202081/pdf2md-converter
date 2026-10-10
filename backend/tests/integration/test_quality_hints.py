"""Quality inventory is created for new jobs only and carries honest signals."""

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.models import Base, Job, PageQualityFlag, User
from app.domain.chapters import ChapterInfo
from app.domain.enums import JobStatus
from app.domain.results import PipelineContext
from app.services.pipeline.stages.quality_hints import QualityHintsStage


async def test_hints_are_persisted_with_source_pages_and_mark_inventory_ready() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            User(id=1, username="admin", password_hash="hash", role="ADMIN", status="ACTIVE")
        )
        session.add(
            Job(id="new-job", filename="book.pdf", stored_path="book.pdf", status=JobStatus.RUNNING)
        )
        await session.commit()
        ctx = PipelineContext(
            job_id="new-job",
            upload_path="book.pdf",
            output_dir="out",
            settings=Settings(),
            data={
                "chapters": [ChapterInfo(1, "One", "01_One", 0, 1)],
                "quality_hints": {
                    1: {"OCR_USED": "Scanned page: OCR fallback was used"},
                    2: {"LOW_TEXT": "Very little text was extracted; check this page"},
                },
            },
        )
        stage = QualityHintsStage(SimpleNamespace(), session)
        assert (await stage.execute(ctx))["warnings"] == 2
        job = await session.get(Job, "new-job")
        assert job is not None and job.quality_inventory_ready
        flags = (
            await session.scalars(select(PageQualityFlag).order_by(PageQualityFlag.page_number))
        ).all()
        assert [(f.page_number, f.chapter_order, f.code) for f in flags] == [
            (1, 1, "OCR_USED"),
            (2, 1, "LOW_TEXT"),
        ]
        # Idempotent on re-run; old jobs remain inventory_ready=False by migration default.
        assert (await stage.execute(ctx))["warnings"] == 2
        assert len((await session.scalars(select(PageQualityFlag))).all()) == 2
    await engine.dispose()
