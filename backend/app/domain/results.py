"""Value objects passed between pipeline stages."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import Settings
from app.domain.enums import StageStatus


@dataclass(slots=True)
class StageResult:
    """Outcome of a single pipeline stage execution."""

    stage: str
    status: StageStatus
    started_at: datetime
    finished_at: datetime
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PipelineContext:
    """Shared context handed to every stage of one conversion job.

    Stages communicate exclusively through ``data`` — never through globals
    or side channels — which keeps stages independent and testable.
    """

    job_id: str
    upload_path: str
    output_dir: str
    settings: Settings
    data: dict[str, Any] = field(default_factory=dict)
