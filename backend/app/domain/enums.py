"""Domain enumerations."""

import enum


class JobStatus(enum.StrEnum):
    """Lifecycle states of a conversion job."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"  # finished, but some stages were skipped/failed softly


class StageStatus(enum.StrEnum):
    """Lifecycle states of a single pipeline stage."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


TERMINAL_JOB_STATES: frozenset[JobStatus] = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.PARTIAL}
)
