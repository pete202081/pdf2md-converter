"""Job-scoped diagram breaker and orchestrator cooperative-stop invariants."""

from pathlib import Path

import fakeredis.aioredis
import pytest

from app.adapters.storage.file_store import LocalFileStore
from app.config import Settings
from app.db.models import Job, utcnow
from app.domain.enums import JobStatus
from app.domain.results import PipelineContext
from app.services.cancellation import JobCancellation
from app.services.pipeline.blocks import IMAGE, ContentBlock
from app.services.pipeline.orchestrator import ConversionOrchestrator
from app.services.pipeline.stages.convert_mermaid import ConvertMermaidStage
from app.services.progress_service import ProgressService
from tests.fakes import (
    FakeVlmProvider,
    InMemoryJobRepository,
    InMemoryProgressRepository,
    InMemoryStageRepository,
)
from tests.helpers import flowchart_png


def progress() -> ProgressService:
    return ProgressService(
        InMemoryProgressRepository(), fakeredis.aioredis.FakeRedis(decode_responses=True)
    )


@pytest.mark.parametrize("first_error", [True, False])
async def test_diagram_breaker_after_aggregate_failure(tmp_path: Path, first_error: bool) -> None:
    store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    await store.save_output_bytes("job1", "assets/one.png", flowchart_png())
    await store.save_output_bytes("job1", "assets/two.png", flowchart_png())
    # Missing first asset is not a provider failure; next candidate must still be tried.
    assets = ["assets/one.png" if first_error else "assets/missing.png", "assets/two.png"]
    blocks = [
        ContentBlock(kind=IMAGE, page=i, y=0.5, image_path=asset) for i, asset in enumerate(assets)
    ]
    provider = FakeVlmProvider(fail=True)
    ctx = PipelineContext("job1", "unused", store.get_output_root("job1"), Settings())
    ctx.data = {"blocks": blocks, "diagram_candidates": [0, 1]}
    metrics = await ConvertMermaidStage(progress(), store, provider).execute(ctx)
    assert provider.calls == 1
    assert metrics["fallback"] == 2
    assert metrics["vision_disabled"] is True
    assert [item["reason"] for item in ctx.data["diagram_reviews"]] == (
        ["vision_or_validation_failed", "vision_unavailable"]
        if first_error
        else ["asset_error", "vision_or_validation_failed"]
    )
    assert all(block.kind == IMAGE and block.review_id for block in blocks)
    assert ctx.data["partial"] is True


async def test_cancel_before_claim_and_during_stage(tmp_path: Path) -> None:
    repo = InMemoryJobRepository()
    stages = InMemoryStageRepository()
    store = LocalFileStore(str(tmp_path / "u"), str(tmp_path / "o"))
    job = Job(id="job1", filename="book.pdf", stored_path="unused", status=JobStatus.QUEUED)
    await repo.create(job)

    async def check() -> None:
        if job.cancel_requested_at:
            raise JobCancellation()

    pipeline = ConversionOrchestrator([], repo, stages, store, Settings(), cancellation_check=check)
    job.cancel_requested_at = utcnow()
    with pytest.raises(JobCancellation):
        await pipeline.run("job1")
    assert job.status == JobStatus.QUEUED
    assert not stages.stages

    job.cancel_requested_at = None

    class CancelStage(ConvertMermaidStage):
        async def execute(self, ctx: PipelineContext) -> dict[str, object]:
            job.cancel_requested_at = utcnow()
            await ctx.ensure_active()
            return {}

    pipeline = ConversionOrchestrator(
        [CancelStage(progress(), store, FakeVlmProvider())],
        repo,
        stages,
        store,
        Settings(),
        cancellation_check=check,
    )
    with pytest.raises(JobCancellation):
        await pipeline.run("job1")
    assert job.status == JobStatus.RUNNING
    assert len(stages.stages) == 1


async def test_finalization_race_never_marks_cancelled_job_success(tmp_path: Path) -> None:
    repo = InMemoryJobRepository()
    job = Job(id="job2", filename="book.pdf", stored_path="unused", status=JobStatus.QUEUED)
    await repo.create(job)
    original_finalize = repo.finalize

    async def losing_finalize(job_id, status, **fields):
        job.cancel_requested_at = utcnow()
        return await original_finalize(job_id, status, **fields)

    repo.finalize = losing_finalize  # type: ignore[method-assign]
    pipeline = ConversionOrchestrator(
        [],
        repo,
        InMemoryStageRepository(),
        LocalFileStore(str(tmp_path / "u"), str(tmp_path / "o")),
        Settings(),
    )
    with pytest.raises(JobCancellation):
        await pipeline.run("job2")
    assert job.status == JobStatus.RUNNING
