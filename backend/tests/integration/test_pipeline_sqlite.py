"""End-to-end pipeline test: real repositories on SQLite + fakeredis.

Builds a synthetic 4-page book (TOC with 2 chapters, a table, an image and a
scanned page), runs the FULL Phase-2 pipeline through the orchestrator and
verifies: chapter detection, chapter folders in order, Markdown content
(headings, table, image embed, OCR fallback), the chapters.json manifest,
chapter DB rows and the final ZIP package.

Runs in CI without Docker, Postgres, Redis or Tesseract (OCR is faked).
"""

import json
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.adapters.storage.file_store import LocalFileStore
from app.config import Settings
from app.db.models import Base, Job
from app.domain.enums import JobStatus, StageStatus
from app.repositories.chapter_repository import ChapterRepository
from app.repositories.diagram_review_repository import DiagramReviewRepository
from app.repositories.job_repository import JobRepository
from app.repositories.progress_repository import ProgressRepository
from app.repositories.stage_repository import StageRepository
from app.services.diagram_review import DiagramReviewService
from app.services.image_extractor import ImageExtractor
from app.services.pipeline.orchestrator import ConversionOrchestrator
from app.services.pipeline.stages.assemble_chapters import AssembleChaptersStage
from app.services.pipeline.stages.classify_diagrams import ClassifyDiagramsStage
from app.services.pipeline.stages.convert_mermaid import ConvertMermaidStage
from app.services.pipeline.stages.detect_chapters import DetectChaptersStage
from app.services.pipeline.stages.extract_images import ExtractImagesStage
from app.services.pipeline.stages.extract_tables import ExtractTablesStage
from app.services.pipeline.stages.extract_text import ExtractTextStage
from app.services.pipeline.stages.package_output import PackageOutputStage
from app.services.pipeline.stages.validate_upload import ValidateUploadStage
from app.services.progress_service import ProgressService
from app.services.text_extractor import TextExtractor
from tests.fakes import FakeOcrEngine, FakeVlmProvider
from tests.helpers import build_book_pdf


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _build_stages(
    progress, file_store, chapter_repository, ocr_engine, vlm_engine=None, review_repository=None
):
    return [
        ValidateUploadStage(progress),
        DetectChaptersStage(progress),
        ExtractTextStage(progress, TextExtractor(ocr_engine)),
        ExtractTablesStage(progress),
        ExtractImagesStage(progress, ImageExtractor(file_store)),
        ClassifyDiagramsStage(progress, file_store),
        ConvertMermaidStage(progress, file_store, vlm_engine or FakeVlmProvider()),
        AssembleChaptersStage(
            progress, file_store, chapter_repository, review_repository=review_repository
        ),
        PackageOutputStage(progress),
    ]


async def test_full_pipeline_end_to_end(session: AsyncSession, tmp_path: Path) -> None:
    pdf_path = Path(build_book_pdf(tmp_path / "book.pdf"))
    settings = Settings()
    file_store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    job_repository = JobRepository(session)
    stage_repository = StageRepository(session)
    progress_repository = ProgressRepository(session)
    chapter_repository = ChapterRepository(session)

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    progress = ProgressService(progress_repository, redis)
    ocr = FakeOcrEngine(text="Scanned page text")

    await job_repository.create(
        Job(
            id="job-1",
            filename="book.pdf",
            stored_path=str(pdf_path),
            status=JobStatus.QUEUED,
        )
    )

    orchestrator = ConversionOrchestrator(
        stages=_build_stages(progress, file_store, chapter_repository, ocr),
        job_repository=job_repository,
        stage_repository=stage_repository,
        file_store=file_store,
        settings=settings,
    )

    status = await orchestrator.run("job-1")

    assert status == JobStatus.SUCCEEDED

    # --- job row ------------------------------------------------------------
    job = await job_repository.get("job-1")
    assert job is not None
    assert job.status == JobStatus.SUCCEEDED
    assert job.page_count == 4
    assert job.chapter_count == 2
    assert job.output_path is not None and job.output_path.endswith(".zip")
    assert job.error is None

    # --- all stages succeeded ------------------------------------------------
    assert [stage.stage_name for stage in job.stages] == [
        "validate_upload",
        "detect_chapters",
        "extract_text",
        "extract_tables",
        "extract_images",
        "classify_diagrams",
        "convert_mermaid",
        "assemble_chapters",
        "package_output",
    ]
    assert all(stage.status == StageStatus.SUCCEEDED for stage in job.stages)

    # --- chapter rows in correct order ---------------------------------------
    chapters = await chapter_repository.list_for_job("job-1")
    assert [chapter.folder_name for chapter in chapters] == [
        "01_Introduction",
        "02_Getting_Started",
    ]
    assert [chapter.order_index for chapter in chapters] == [1, 2]
    assert chapters[1].image_count == 1
    assert chapters[1].word_count > 0

    # --- chapter files on disk ------------------------------------------------
    output_root = Path(file_store.get_output_root("job-1"))
    intro_md = output_root / "01_Introduction" / "chapter.md"
    started_md = output_root / "02_Getting_Started" / "chapter.md"
    assert intro_md.is_file()
    assert started_md.is_file()

    intro = intro_md.read_text(encoding="utf-8")
    assert 'title: "Introduction"' in intro
    assert "# Introduction" in intro
    assert "Welcome to the book body text." in intro

    started = started_md.read_text(encoding="utf-8")
    assert "# Getting Started" in started
    # table rendered as a Markdown table
    assert "| Name | Age | City |" in started
    assert "| Alice | 30 | Paris |" in started
    assert "NameAgeCity" not in started  # table cells are not duplicated as flattened text
    assert "Alice30Paris" not in started
    # image embedded with a chapter-relative link
    assert "](../assets/img_p3_x" in started
    # scanned (blank) page went through the OCR fallback
    assert "Scanned page text" in started
    assert ocr.calls == 1

    # the image asset exists
    assets = list((output_root / "assets").glob("*"))
    assert len(assets) == 1

    # --- manifest --------------------------------------------------------------
    manifest = json.loads((output_root / "chapters.json").read_text(encoding="utf-8"))
    assert manifest["chapter_count"] == 2
    assert [entry["folder"] for entry in manifest["chapters"]] == [
        "01_Introduction",
        "02_Getting_Started",
    ]
    assert manifest["chapters"][1]["source_pages"] == [2, 4]

    # --- ZIP package -------------------------------------------------------------
    zip_path = Path(job.output_path)
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "chapters.json" in names
    assert "01_Introduction/chapter.md" in names
    assert "02_Getting_Started/chapter.md" in names
    assert any(name.startswith("assets/") for name in names)

    # --- progress events were recorded -------------------------------------------
    events = await progress_repository.recent("job-1", limit=500)
    assert {event.stage for event in events} >= {
        "detect_chapters",
        "extract_text",
        "extract_tables",
        "extract_images",
        "classify_diagrams",
        "convert_mermaid",
        "assemble_chapters",
        "package_output",
    }


async def test_pipeline_failure_is_persisted(session: AsyncSession, tmp_path: Path) -> None:
    missing_pdf = tmp_path / "missing.pdf"  # never created

    job_repository = JobRepository(session)
    stage_repository = StageRepository(session)
    progress_repository = ProgressRepository(session)
    chapter_repository = ChapterRepository(session)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    progress = ProgressService(progress_repository, redis)
    file_store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))

    await job_repository.create(
        Job(
            id="job-bad",
            filename="missing.pdf",
            stored_path=str(missing_pdf),
            status=JobStatus.QUEUED,
        )
    )

    orchestrator = ConversionOrchestrator(
        stages=_build_stages(progress, file_store, chapter_repository, FakeOcrEngine()),
        job_repository=job_repository,
        stage_repository=stage_repository,
        file_store=file_store,
        settings=Settings(),
    )

    status = await orchestrator.run("job-bad")

    assert status == JobStatus.FAILED
    reloaded = await job_repository.get("job-bad")
    assert reloaded is not None
    assert reloaded.status == JobStatus.FAILED
    assert "validate_upload" in (reloaded.error or "")
    assert reloaded.stages[0].status == StageStatus.FAILED
    assert reloaded.stages[0].error is not None


@pytest.mark.parametrize("fail", [False, True])
async def test_mermaid_pipeline_success_or_image_fallback(
    session: AsyncSession, tmp_path: Path, fail: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two placements of one diagram: transcribe once, render twice; retain asset."""
    import pymupdf

    from tests.helpers import flowchart_png

    image_file = tmp_path / "flowchart.png"
    image_file.write_bytes(flowchart_png())
    pdf_path = tmp_path / "diagram.pdf"
    document = pymupdf.open()
    for page_number in range(2):
        page = document.new_page()
        if page_number == 0:
            page.insert_text((72, 72), "Diagrams", fontsize=20)
        page.insert_text((72, 100), "Diagram description in the text.", fontsize=11)
        page.insert_image(pymupdf.Rect(72, 130, 372, 330), filename=str(image_file))
    document.set_toc([[1, "Diagrams", 1]])
    document.save(str(pdf_path))
    document.close()

    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    jobs = JobRepository(session)
    stages = StageRepository(session)
    chapters = ChapterRepository(session)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    progress = ProgressService(ProgressRepository(session), redis)
    vlm = FakeVlmProvider(fail=fail)
    job_id = "diagram-fail" if fail else "diagram-good"
    await jobs.create(
        Job(id=job_id, filename="diagram.pdf", stored_path=str(pdf_path), status=JobStatus.QUEUED)
    )
    pipeline = ConversionOrchestrator(
        stages=_build_stages(
            progress, store, chapters, FakeOcrEngine(), vlm, DiagramReviewRepository(session)
        ),
        job_repository=jobs,
        stage_repository=stages,
        file_store=store,
        settings=Settings(),
    )
    status = await pipeline.run(job_id)
    assert status == (JobStatus.PARTIAL if fail else JobStatus.SUCCEEDED)
    assert vlm.calls == 1  # reused xref across two pages
    reloaded = await jobs.get(job_id)
    assert reloaded is not None
    assert reloaded.output_path is not None
    assert all(stage.status == StageStatus.SUCCEEDED for stage in reloaded.stages)
    convert = next(stage for stage in reloaded.stages if stage.stage_name == "convert_mermaid")
    assert convert.metrics == {
        "candidates": 2,
        "converted": 0 if fail else 2,
        "fallback": 2 if fail else 0,
    }

    rows = await chapters.list_for_job(job_id)
    assert len(rows) == 1
    assert rows[0].mermaid_count == (0 if fail else 2)
    assert rows[0].image_count == 2  # originals remain linked or embedded
    markdown = Path(rows[0].md_path).read_text(encoding="utf-8")
    if fail:
        assert "```mermaid" not in markdown
        assert markdown.count("![") == 2
        assert reloaded.error == "2 diagram(s) kept as images"
        assert reloaded.review_total == 2 and reloaded.review_pending == 2
        assert {item.page_number for item in reloaded.review_items} == {1, 2}
        assert len({item.id for item in reloaded.review_items}) == 2
        assert len({item.asset_name for item in reloaded.review_items}) == 1
        assert markdown.count("<!-- pdf2md-review:") == 4
    else:
        assert markdown.count("```mermaid") == 2
        assert "A[Start] --> B[Finish]" in markdown
        assert markdown.count("[Original diagram image]") == 2
        assert reloaded.error is None
    with zipfile.ZipFile(reloaded.output_path) as archive:
        assert len([name for name in archive.namelist() if name.startswith("assets/")]) == 1
        manifest = json.loads(archive.read("chapters.json"))
        assert manifest["chapters"][0]["mermaid_count"] == rows[0].mermaid_count
    if fail:
        from app.domain.exceptions import ValidationError

        review = DiagramReviewService(session, store)
        first, second = sorted(reloaded.review_items, key=lambda item: item.page_number)
        old_zip = reloaded.output_path
        await review.decide(job_id, first.id, "ACCEPTED_IMAGE")
        assert reloaded.review_pending == 1
        assert reloaded.output_path == old_zip
        with pytest.raises(ValidationError):
            await review.decide(job_id, second.id, "EDITED_MERMAID", "click A javascript:evil")
        assert reloaded.output_path == old_zip
        import app.services.diagram_review as review_module

        def fail_zip(*args: object) -> None:
            raise OSError("simulated disk failure")

        with monkeypatch.context() as patch:
            patch.setattr(review_module, "_write_revision", fail_zip)
            with pytest.raises(OSError, match="simulated disk failure"):
                await review.decide(job_id, second.id, "EDITED_MERMAID", "flowchart TD\nA --> B")
        reloaded = await jobs.get(job_id)
        assert reloaded is not None and reloaded.output_path == old_zip
        revisions = Path(store.get_output_root(job_id)) / ".revisions"
        assert not revisions.exists() or not any(revisions.iterdir())
        await review.decide(
            job_id, second.id, "EDITED_MERMAID", "flowchart TD\nA[Start] --> B[End]"
        )
        assert reloaded.review_pending == 0
        assert reloaded.status == JobStatus.PARTIAL  # historical conversion result
        assert reloaded.output_path != old_zip
        assert Path(old_zip).exists()  # old revision remains valid for in-flight readers
        with zipfile.ZipFile(reloaded.output_path) as updated_zip:
            edited_md = updated_zip.read(f"{rows[0].folder_name}/chapter.md").decode()
            assert edited_md.count("```mermaid") == 1
            assert edited_md.count("![") == 1  # duplicate asset occurrence untouched
            assert "[Original diagram image]" in edited_md
            edited_manifest = json.loads(updated_zip.read("chapters.json"))
            assert edited_manifest["chapters"][0]["mermaid_count"] == 1
        await review.decide(job_id, second.id, "ACCEPTED_IMAGE")
        with zipfile.ZipFile(reloaded.output_path) as reverted_zip:
            assert (
                "```mermaid" not in reverted_zip.read(f"{rows[0].folder_name}/chapter.md").decode()
            )
            assert (
                json.loads(reverted_zip.read("chapters.json"))["chapters"][0]["mermaid_count"] == 0
            )
