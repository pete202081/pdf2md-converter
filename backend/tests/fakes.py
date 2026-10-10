"""In-memory repository fakes for unit tests.

They implement the same repository ports as the SQLAlchemy adapters, which is
exactly what Dependency Inversion buys us: services and the orchestrator are
tested without a database.
"""

from typing import Any

from app.adapters.ocr.base import IOcrEngine
from app.adapters.vlm.base import IVlmProvider
from app.db.models import Chapter, Job, JobStage, ProgressEvent
from app.domain.enums import JobStatus, StageStatus
from app.domain.exceptions import ProcessingError, ProviderError
from app.repositories.base import (
    IChapterRepository,
    IJobRepository,
    IProgressRepository,
    IStageRepository,
)


class FakeOcrEngine(IOcrEngine):
    """OCR fake returning canned text; records how often it was called."""

    def __init__(self, text: str = "OCR extracted text") -> None:
        self.text = text
        self.calls = 0

    def ocr_image(self, image_bytes: bytes) -> str:
        self.calls += 1
        return self.text


class FailingOcrEngine(IOcrEngine):
    """OCR fake that always fails (e.g. tesseract binary missing)."""

    def ocr_image(self, image_bytes: bytes) -> str:
        raise ProcessingError("OCR failed")


class FakeVlmProvider(IVlmProvider):
    """Deterministic vision provider for end-to-end tests; no network calls."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def diagram_to_mermaid(self, image: bytes, mime_type: str) -> str:
        self.calls += 1
        assert mime_type == "image/png"
        assert image.startswith(b"\x89PNG")
        if self.fail:
            raise ProviderError("No vision model available")
        return "flowchart TD\nA[Start] --> B[Finish]"


class InMemoryJobRepository(IJobRepository):
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def create(self, job: Job) -> Job:
        self.jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def list(
        self, *, limit: int = 50, offset: int = 0, owner_id: int | None = None
    ) -> list[Job]:
        jobs = [job for job in self.jobs.values() if owner_id is None or job.owner_id == owner_id]
        return jobs[offset : offset + limit]

    async def update_status(self, job_id: str, status: JobStatus, **fields: Any) -> Job | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job.status = status
        for key, value in fields.items():
            setattr(job, key, value)
        return job

    async def delete(self, job_id: str) -> bool:
        return self.jobs.pop(job_id, None) is not None


class InMemoryStageRepository(IStageRepository):
    def __init__(self) -> None:
        self.stages: dict[int, JobStage] = {}
        self._next_id = 1

    async def create(self, *, job_id: str, stage_name: str, status: StageStatus) -> JobStage:
        stage = JobStage(
            id=self._next_id,
            job_id=job_id,
            stage_name=stage_name,
            status=status,
            metrics={},
        )
        self._next_id += 1
        self.stages[stage.id] = stage
        return stage

    async def mark(self, stage_id: int, status: StageStatus, **fields: Any) -> JobStage | None:
        stage = self.stages.get(stage_id)
        if stage is None:
            return None
        stage.status = status
        for key, value in fields.items():
            setattr(stage, key, value)
        return stage


class InMemoryProgressRepository(IProgressRepository):
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    async def add(self, event: ProgressEvent) -> ProgressEvent:
        self.events.append(event)
        return event

    async def recent(self, job_id: str, *, limit: int = 100) -> list[ProgressEvent]:
        matching = [event for event in self.events if event.job_id == job_id]
        return matching[-limit:]


class InMemoryChapterRepository(IChapterRepository):
    def __init__(self) -> None:
        self.chapters: dict[str, list[Chapter]] = {}

    async def create(self, chapter: Chapter) -> Chapter:
        self.chapters.setdefault(chapter.job_id, []).append(chapter)
        return chapter

    async def list_for_job(self, job_id: str) -> list[Chapter]:
        return sorted(self.chapters.get(job_id, []), key=lambda chapter: chapter.order_index)
