"""Tests for the conversion orchestrator (sequencing + failure policy)."""

from pathlib import Path
from typing import Any

import fakeredis.aioredis
import pytest

from app.adapters.storage.file_store import LocalFileStore
from app.config import Settings
from app.db.models import Job
from app.domain.enums import JobStatus, StageStatus
from app.domain.exceptions import NotFoundError, ValidationError
from app.domain.results import PipelineContext
from app.services.pipeline.orchestrator import ConversionOrchestrator
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService
from tests.fakes import (
    InMemoryJobRepository,
    InMemoryProgressRepository,
    InMemoryStageRepository,
)


class SucceedStage(BasePipelineStage):
    name = "succeed"

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        ctx.data["page_count"] = 7
        return {"ok": True}


class AppFailStage(BasePipelineStage):
    name = "app_fail"

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        raise ValidationError("bad input")


class CrashStage(BasePipelineStage):
    name = "crash"

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        raise RuntimeError("boom")


class MustNotRunStage(BasePipelineStage):
    name = "must_not_run"

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        raise AssertionError("this stage should never execute")


def make_progress() -> ProgressService:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return ProgressService(InMemoryProgressRepository(), redis)


def make_orchestrator(
    tmp_path: Path, stages: list[BasePipelineStage]
) -> tuple[ConversionOrchestrator, InMemoryJobRepository, InMemoryStageRepository]:
    job_repository = InMemoryJobRepository()
    stage_repository = InMemoryStageRepository()
    orchestrator = ConversionOrchestrator(
        stages=stages,
        job_repository=job_repository,
        stage_repository=stage_repository,
        file_store=LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs")),
        settings=Settings(),
    )
    return orchestrator, job_repository, stage_repository


async def test_successful_run_marks_job_and_stages_succeeded(tmp_path: Path) -> None:
    progress = make_progress()
    orchestrator, job_repository, stage_repository = make_orchestrator(
        tmp_path, [SucceedStage(progress)]
    )
    await job_repository.create(
        Job(id="j1", filename="b.pdf", stored_path="/tmp/b.pdf", status=JobStatus.QUEUED)
    )

    status = await orchestrator.run("j1")

    assert status == JobStatus.SUCCEEDED
    job = await job_repository.get("j1")
    assert job is not None
    assert job.status == JobStatus.SUCCEEDED
    assert job.page_count == 7  # synced from the stage context
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.error is None
    assert len(stage_repository.stages) == 1
    stage = next(iter(stage_repository.stages.values()))
    assert stage.status == StageStatus.SUCCEEDED
    assert stage.metrics == {"ok": True}


async def test_app_error_aborts_pipeline_and_fails_job(tmp_path: Path) -> None:
    progress = make_progress()
    orchestrator, job_repository, stage_repository = make_orchestrator(
        tmp_path, [AppFailStage(progress), MustNotRunStage(progress)]
    )
    await job_repository.create(
        Job(id="j2", filename="b.pdf", stored_path="/tmp/b.pdf", status=JobStatus.QUEUED)
    )

    status = await orchestrator.run("j2")

    assert status == JobStatus.FAILED
    job = await job_repository.get("j2")
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert "app_fail" in (job.error or "")
    # Abort policy: the failing stage is recorded, the next one never ran
    assert len(stage_repository.stages) == 1
    stage = next(iter(stage_repository.stages.values()))
    assert stage.stage_name == "app_fail"
    assert stage.status == StageStatus.FAILED
    assert stage.error == "bad input"


async def test_unexpected_exception_is_captured_as_stage_failure(tmp_path: Path) -> None:
    progress = make_progress()
    orchestrator, job_repository, stage_repository = make_orchestrator(
        tmp_path, [CrashStage(progress)]
    )
    await job_repository.create(
        Job(id="j3", filename="b.pdf", stored_path="/tmp/b.pdf", status=JobStatus.QUEUED)
    )

    status = await orchestrator.run("j3")

    assert status == JobStatus.FAILED
    stage = next(iter(stage_repository.stages.values()))
    assert stage.status == StageStatus.FAILED
    assert stage.error == "Unexpected error in stage"


async def test_unknown_job_raises_not_found(tmp_path: Path) -> None:
    progress = make_progress()
    orchestrator, _, _ = make_orchestrator(tmp_path, [SucceedStage(progress)])

    with pytest.raises(NotFoundError):
        await orchestrator.run("does-not-exist")
