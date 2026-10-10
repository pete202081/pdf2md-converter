"""Server-Sent Events endpoint for live job progress."""

import json
import secrets
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_progress_repository, get_session
from app.api.v1.routers.auth import check_owner, digest, expired, require_user
from app.container import Container
from app.db.models import User, UserSession
from app.domain.enums import TERMINAL_JOB_STATES
from app.repositories.base import IProgressRepository
from app.services.progress_service import events_channel

router = APIRouter(prefix="/api/v1/jobs", tags=["events"])

_KEEPALIVE_SECONDS = 5.0


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: str,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
    progress_repository: IProgressRepository = Depends(get_progress_repository),
) -> StreamingResponse:
    """Stream progress events for a job over SSE.

    Replays recent persisted events first (so late/re-connecting clients see
    history), then follows the live Redis pub/sub feed until the job reaches a
    terminal state or the client disconnects.
    """
    job_repository = container.job_repository(session)
    job = check_owner(await job_repository.get(job_id), user)

    # Distributed bounded SSE connections, with expiring slots after process crashes.
    lease_token = secrets.token_urlsafe(16)
    lease_key = ""
    for slot in range(max(1, container.settings.sse_limit)):
        candidate = f"pdf2md:sse:{user.id}:{slot}"
        if await container.redis.set(candidate, lease_token, nx=True, ex=60):
            lease_key = candidate
            break
    if not lease_key:
        raise HTTPException(429, "Too many live streams", headers={"Retry-After": "60"})

    # Replay history before opening the stream (session is used here only).
    replay = await progress_repository.recent(job_id, limit=200)
    replayed = [
        {
            "job_id": event.job_id,
            "stage": event.stage,
            "percent": event.percent,
            "message": event.message,
            "ts": event.ts.isoformat() if event.ts else None,
            "terminal": False,
        }
        for event in replay
    ]

    token = request.cookies.get("pdf2md_session", "")

    async def still_authorized() -> bool:
        # A disabled account must stop receiving an already-open SSE feed.
        async with container.database.session() as current_db:
            found = await current_db.get(UserSession, digest(token))
            if not found or expired(found.expires_at):
                return False
            current_user = await current_db.get(User, found.user_id)
            return bool(
                current_user
                and current_user.status == "ACTIVE"
                and (current_user.role == "ADMIN" or job.owner_id == current_user.id)
            )

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in deliver():
                yield event
        finally:
            if await container.redis.get(lease_key) == lease_token:
                await container.redis.delete(lease_key)

    async def deliver() -> AsyncIterator[str]:
        if not await still_authorized():
            return
        for payload in replayed:
            yield f"event: progress\ndata: {json.dumps(payload)}\n\n"

        if job.status in TERMINAL_JOB_STATES:
            yield _terminal_event(job_id, job.status.value)
            return

        channel = events_channel(job_id)
        pubsub = container.redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                if await request.is_disconnected():
                    return
                if await container.redis.get(lease_key) != lease_token:
                    return
                await container.redis.expire(lease_key, 60)
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=_KEEPALIVE_SECONDS
                )
                if not await still_authorized():
                    return
                if message is None:
                    yield ": keepalive\n\n"
                    continue
                data = message["data"]
                yield f"event: progress\ndata: {data}\n\n"
                try:
                    terminal = bool(json.loads(data).get("terminal"))
                except (ValueError, TypeError):
                    terminal = False
                if terminal:
                    return
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
        },
    )


def _terminal_event(job_id: str, status: str) -> str:
    payload = {
        "job_id": job_id,
        "stage": "orchestrator",
        "percent": 100,
        "message": f"Job finished with status {status}",
        "ts": None,
        "terminal": True,
    }
    return f"event: progress\ndata: {json.dumps(payload)}\n\n"
