"""Internal Prometheus endpoint (never forwarded through nginx)."""

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session
from app.container import Container
from app.db.models import Job

router = APIRouter(tags=["internal"])


@router.get("/metrics", include_in_schema=False)
async def metrics(
    session: AsyncSession = Depends(get_session), container: Container = Depends(get_container)
) -> Response:
    registry = CollectorRegistry()
    jobs = Gauge("pdf2md_jobs", "Jobs by current outcome", ["status"], registry=registry)
    counts = await session.execute(select(Job.status, func.count()).group_by(Job.status))
    for status, count in counts:
        jobs.labels(status.value).set(count)
    stages = Gauge(
        "pdf2md_stage_duration_seconds_sum",
        "Total completed stage time",
        ["stage"],
        registry=registry,
    )
    stage_counts = Gauge(
        "pdf2md_stage_duration_seconds_count", "Completed stages", ["stage"], registry=registry
    )
    fallback = Gauge(
        "pdf2md_provider_fallbacks_total", "Diagram vision fallback occurrences", registry=registry
    )
    errors = Gauge("pdf2md_provider_errors_total", "Diagram stage errors", registry=registry)
    provider_failures = Gauge(
        "pdf2md_provider_failures_total",
        "Provider failures by kind",
        ["provider"],
        registry=registry,
    )
    failures = await container.redis.hgetall("pdf2md:metrics:provider_failure")  # type: ignore[misc]
    for name, count in failures.items():
        if name in {"ollama", "openrouter", "openai", "anthropic"}:
            provider_failures.labels(name).set(int(count))
    queue = Gauge("pdf2md_queue_depth", "Pending Celery jobs", registry=registry)
    active = Gauge("pdf2md_worker_active_jobs", "Worker jobs in progress", registry=registry)
    worker_healthy = Gauge("pdf2md_worker_healthy", "Worker heartbeat is fresh", registry=registry)
    stage_ms = await container.redis.hgetall("pdf2md:metrics:stage_ms")  # type: ignore[misc]
    for name, value in stage_ms.items():
        if name in {
            "validate_upload",
            "detect_chapters",
            "extract_text",
            "extract_tables",
            "extract_images",
            "classify_diagrams",
            "convert_mermaid",
            "assemble_chapters",
            "package_output",
            "publish_objects",
        }:
            stages.labels(name).set(float(value) / 1000)
    counts_by_stage = await container.redis.hgetall("pdf2md:metrics:stage_count")  # type: ignore[misc]
    for name, value in counts_by_stage.items():
        if name in {
            "validate_upload",
            "detect_chapters",
            "extract_text",
            "extract_tables",
            "extract_images",
            "classify_diagrams",
            "convert_mermaid",
            "assemble_chapters",
            "package_output",
            "publish_objects",
        }:
            stage_counts.labels(name).set(int(value))
    fallback.set(int(await container.redis.get("pdf2md:metrics:fallbacks") or 0))
    errors.set(int(await container.redis.get("pdf2md:metrics:provider_errors") or 0))
    queue.set(await container.redis.llen("celery"))  # type: ignore[misc]
    active.set(int(await container.redis.get("pdf2md:metrics:active") or 0))
    worker_healthy.set(int(bool(await container.redis.exists("pdf2md:metrics:worker:heartbeat"))))
    return Response(generate_latest() + generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
