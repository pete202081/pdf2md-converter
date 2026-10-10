"""Repository ports (abstract interfaces)."""

from abc import ABC, abstractmethod
from typing import Any

from app.db.models import Chapter, DiagramReviewItem, Job, JobStage, ProgressEvent
from app.domain.enums import JobStatus, StageStatus


class IJobRepository(ABC):
    """Persistence port for conversion jobs."""

    @abstractmethod
    async def create(self, job: Job) -> Job:
        """Persist a new job."""

    @abstractmethod
    async def get(self, job_id: str) -> Job | None:
        """Fetch a job with its stages and chapters eagerly loaded."""

    @abstractmethod
    async def list(self, *, limit: int = 50, offset: int = 0) -> list[Job]:
        """List jobs, newest first."""

    @abstractmethod
    async def update_status(self, job_id: str, status: JobStatus, **fields: Any) -> Job | None:
        """Update the job status plus any extra column values (page_count, error, …)."""

    @abstractmethod
    async def delete(self, job_id: str) -> bool:
        """Delete a job and its children. Returns False if it did not exist."""


class IStageRepository(ABC):
    """Persistence port for per-stage execution records."""

    @abstractmethod
    async def create(self, *, job_id: str, stage_name: str, status: StageStatus) -> JobStage:
        """Persist a new stage record."""

    @abstractmethod
    async def mark(self, stage_id: int, status: StageStatus, **fields: Any) -> JobStage | None:
        """Update a stage record (status, timestamps, error, metrics)."""


class IProgressRepository(ABC):
    """Persistence port for progress events."""

    @abstractmethod
    async def add(self, event: ProgressEvent) -> ProgressEvent:
        """Persist a progress event."""

    @abstractmethod
    async def recent(self, job_id: str, *, limit: int = 100) -> list[ProgressEvent]:
        """Return the most recent events for a job, oldest first."""


class IChapterRepository(ABC):
    """Persistence port for generated chapters."""

    @abstractmethod
    async def create(self, chapter: Chapter) -> Chapter:
        """Persist a chapter record."""

    @abstractmethod
    async def list_for_job(self, job_id: str) -> list[Chapter]:
        """Return all chapters of a job in order."""


class IDiagramReviewRepository(ABC):
    @abstractmethod
    async def create(self, item: DiagramReviewItem) -> DiagramReviewItem:
        """Record a specific fallback occurrence after chapter assembly."""
