"""Public visual bookshelves, owner publication and private reader progress."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session
from app.api.v1.routers.auth import require_admin, require_user
from app.container import Container
from app.db.models import (
    ChapterCompletion,
    Job,
    ReadingPosition,
    ShelfBook,
    User,
    utcnow,
)
from app.domain.enums import JobStatus
from app.domain.exceptions import NotFoundError
from app.logging import get_logger
from app.services.rate_limits import RateLimiter
from app.services.shelves import ShelfPublisher, ShelfStorage, _label

router = APIRouter(prefix="/api/v1/shelves", tags=["bookshelves"])
log = get_logger("shelf_api")


def storage(container: Container) -> ShelfStorage:
    return ShelfStorage(container.file_store, container.object_store)


def summary(book: ShelfBook) -> dict[str, Any]:
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "description": book.description,
        "visibility": book.visibility,
        "source_kind": book.source_kind,
        "chapter_count": len(book.chapters),
        "created_at": book.created_at,
    }


def detail(book: ShelfBook) -> dict[str, Any]:
    return {
        **summary(book),
        "chapters": [
            {"id": chapter.id, "order_index": chapter.order_index, "title": chapter.title}
            for chapter in book.chapters
        ],
    }


async def book_for(session: AsyncSession, book_id: str, user_id: int | None) -> ShelfBook:
    # Authorization is applied before loading chapter/asset rows.
    filters = [ShelfBook.id == book_id]
    if user_id is None:
        filters.append(ShelfBook.visibility == "PUBLIC")
    else:
        filters.append((ShelfBook.visibility == "PUBLIC") | (ShelfBook.owner_id == user_id))
    found = await session.scalar(select(ShelfBook).where(*filters))
    if found is None:
        raise NotFoundError("Book not found")
    return found


async def owner_book(
    session: AsyncSession, book_id: str, owner_id: int, lock: bool = False
) -> ShelfBook:
    statement = select(ShelfBook).where(ShelfBook.id == book_id, ShelfBook.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    book = await session.scalar(statement)
    if not book:
        raise NotFoundError("Book not found")
    return book


async def limit_guest(request: Request, container: Container) -> None:
    limiter = RateLimiter(container.redis, container.settings)
    await limiter.check("shelf:public:ip", limiter.remote_ip(request), limit=1200, window=3600)


# Administrators retain read-only inspection, but cannot publish or edit
# someone else's shelf. Keep these routes separate from owner mutations.
@router.get("/admin/books")
async def admin_shelves(
    user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    books = (
        await session.scalars(select(ShelfBook).order_by(ShelfBook.created_at.desc()).limit(200))
    ).all()
    return [{**summary(book), "owner_id": book.owner_id} for book in books]


@router.get("/admin/books/{book_id}")
async def admin_book(
    book_id: str, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    book = await session.get(ShelfBook, book_id)
    if not book:
        raise NotFoundError("Book not found")
    return {**detail(book), "owner_id": book.owner_id}


@router.get("/admin/books/{book_id}/chapters/{chapter_id}")
async def admin_chapter(
    book_id: str,
    chapter_id: int,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> Response:
    book = await session.get(ShelfBook, book_id)
    chapter = next((c for c in book.chapters if c.id == chapter_id), None) if book else None
    if not book or not chapter:
        raise NotFoundError("Chapter not found")
    data = await storage(container).read(book, chapter.relative_path)
    return PlainTextResponse(data.decode("utf-8"), headers={"Cache-Control": "private, no-store"})


@router.get("/admin/books/{book_id}/assets/{name}")
async def admin_asset(
    book_id: str,
    name: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> Response:
    book = await session.get(ShelfBook, book_id)
    asset = next((a for a in book.assets if a.name == name), None) if book else None
    if not book or not asset:
        raise NotFoundError("Asset not found")
    return Response(
        await storage(container).read(book, "assets/" + asset.name),
        media_type=asset.media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/public")
async def list_public(
    request: Request,
    response: Response,
    page: int = 1,
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await limit_guest(request, container)
    response.headers["Cache-Control"] = "no-store"
    page = min(max(page, 1), 1000)
    books = (
        await session.scalars(
            select(ShelfBook)
            .where(ShelfBook.visibility == "PUBLIC")
            .order_by(ShelfBook.created_at.desc(), ShelfBook.id)
            .limit(24)
            .offset((page - 1) * 24)
        )
    ).all()
    return {"books": [summary(book) for book in books], "page": page}


@router.get("/public/{book_id}")
async def public_book(
    book_id: str,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await limit_guest(request, container)
    response.headers["Cache-Control"] = "no-store"
    return detail(await book_for(session, book_id, None))


@router.get("/public/{book_id}/chapters/{chapter_id}")
async def public_chapter(
    book_id: str,
    chapter_id: int,
    request: Request,
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await limit_guest(request, container)
    book = await book_for(session, book_id, None)
    chapter = next((ch for ch in book.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise NotFoundError("Chapter not found")
    raw = await storage(container).read(book, chapter.relative_path)
    return PlainTextResponse(
        raw.decode("utf-8"),
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/public/{book_id}/assets/{name}")
async def public_asset(
    book_id: str,
    name: str,
    request: Request,
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await limit_guest(request, container)
    book = await book_for(session, book_id, None)
    asset = next((a for a in book.assets if a.name == name), None)
    if not asset:
        raise NotFoundError("Asset not found")
    data = await storage(container).read(book, "assets/" + asset.name)
    return Response(
        data,
        media_type=asset.media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/eligible-jobs")
async def eligible_jobs(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> list[dict[str, str]]:
    rows = (
        await session.execute(
            select(Job.id, Job.filename)
            .where(
                Job.owner_id == user.id, Job.status.in_((JobStatus.SUCCEEDED, JobStatus.PARTIAL))
            )
            .order_by(Job.created_at.desc())
            .limit(200)
        )
    ).all()
    return [{"id": job_id, "filename": filename} for job_id, filename in rows]


@router.get("/mine")
async def my_books(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    books = (
        await session.scalars(
            select(ShelfBook)
            .where(ShelfBook.owner_id == user.id)
            .order_by(ShelfBook.created_at.desc())
            .limit(200)
        )
    ).all()
    return [summary(book) for book in books]


@router.get("/mine/{book_id}")
async def my_book(
    book_id: str, user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    return detail(await owner_book(session, book_id, user.id))


@router.get("/mine/{book_id}/chapters/{chapter_id}")
async def my_chapter(
    book_id: str,
    chapter_id: int,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> Response:
    book = await owner_book(session, book_id, user.id)
    chapter = next((ch for ch in book.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise NotFoundError("Chapter not found")
    raw = await storage(container).read(book, chapter.relative_path)
    return PlainTextResponse(raw.decode("utf-8"), headers={"Cache-Control": "private, no-store"})


@router.get("/mine/{book_id}/assets/{name}")
async def my_asset(
    book_id: str,
    name: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> Response:
    book = await owner_book(session, book_id, user.id)
    asset = next((a for a in book.assets if a.name == name), None)
    if not asset:
        raise NotFoundError("Asset not found")
    return Response(
        await storage(container).read(book, "assets/" + name),
        media_type=asset.media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/from-job/{job_id}", status_code=201)
async def add_converted(
    job_id: str,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    await RateLimiter(container.redis, container.settings).costly(request, "upload", user.id)
    book = await ShelfPublisher(session, storage(container)).from_job(job_id, user.id)
    return detail(book)


@router.post("/import", status_code=201)
async def import_markdown(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    await RateLimiter(container.redis, container.settings).costly(request, "upload", user.id)
    book = await ShelfPublisher(session, storage(container)).import_file(
        file, user.id, container.settings.storage_backend, title
    )
    return detail(book)


class ShelfUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    author: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    visibility: Literal["PRIVATE", "PUBLIC"] | None = None


@router.patch("/mine/{book_id}")
async def update_shelf(
    book_id: str,
    body: ShelfUpdate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    book = await owner_book(session, book_id, user.id, lock=True)
    if body.title is not None:
        book.title = _label(body.title, 200, required=True) or book.title
    if body.author is not None:
        book.author = _label(body.author, 120)
    if body.description is not None:
        book.description = _label(body.description, 1000)
    if body.visibility is not None:
        book.visibility = body.visibility
    book.updated_at = utcnow()
    await session.commit()
    log.info("shelf_updated", book_id=book.id, visibility=book.visibility)
    return detail(book)


@router.delete("/mine/{book_id}", status_code=204)
async def remove_shelf(
    book_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    container: Container = Depends(get_container),
) -> Response:
    book = await owner_book(session, book_id, user.id, lock=True)
    chapters = [ch.id for ch in book.chapters]
    if chapters:
        await session.execute(
            delete(ChapterCompletion).where(ChapterCompletion.chapter_id.in_(chapters))
        )
    await session.execute(delete(ReadingPosition).where(ReadingPosition.book_id == book.id))
    await session.delete(book)
    await session.commit()  # revoke all reads before removing stored files
    try:
        await storage(container).remove(book)
    except Exception:
        log.warning("shelf_cleanup_failed", book_id=book.id)
    return Response(status_code=204)


@router.get("/reading/{book_id}")
async def get_progress(
    book_id: str, user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    book = await book_for(session, book_id, user.id)
    position = await session.get(ReadingPosition, (user.id, book_id))
    chapter_ids = [ch.id for ch in book.chapters]
    completed = (
        await session.scalars(
            select(ChapterCompletion.chapter_id).where(
                ChapterCompletion.user_id == user.id, ChapterCompletion.chapter_id.in_(chapter_ids)
            )
        )
    ).all()
    done = set(completed)
    last = position.chapter_id if position and position.chapter_id in chapter_ids else None
    if last is not None and last not in done:
        resume = last
    else:
        remaining = [ch_id for ch_id in chapter_ids if ch_id not in done]
        if not remaining:
            resume = None
        elif last is not None:
            after = [
                ch_id for ch_id in chapter_ids[chapter_ids.index(last) + 1 :] if ch_id in remaining
            ]
            resume = after[0] if after else remaining[0]
        else:
            resume = remaining[0]
    return {
        "last_chapter_id": last,
        "completed_chapter_ids": list(done),
        "resume_chapter_id": resume,
        "all_complete": len(done) == len(chapter_ids),
    }


class ProgressChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_id: int = Field(gt=0)
    completed: bool | None = None


@router.put("/reading/{book_id}")
async def record_progress(
    book_id: str,
    body: ProgressChange,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    book = await book_for(session, book_id, user.id)
    if body.chapter_id not in {ch.id for ch in book.chapters}:
        raise NotFoundError("Chapter not found")
    position = await session.get(ReadingPosition, (user.id, book_id))
    if position is None:
        session.add(
            ReadingPosition(
                user_id=user.id, book_id=book_id, chapter_id=body.chapter_id, updated_at=utcnow()
            )
        )
    else:
        position.chapter_id = body.chapter_id
        position.updated_at = utcnow()
    if body.completed is not None:
        completion = await session.get(ChapterCompletion, (user.id, body.chapter_id))
        if body.completed and completion is None:
            session.add(ChapterCompletion(user_id=user.id, chapter_id=body.chapter_id))
        elif not body.completed and completion is not None:
            await session.delete(completion)
    await session.commit()
    return await get_progress(book_id, user, session)


@router.delete("/reading/{book_id}", status_code=204)
async def clear_progress(
    book_id: str, user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> Response:
    book = await book_for(session, book_id, user.id)
    await session.execute(
        delete(ReadingPosition).where(
            ReadingPosition.user_id == user.id, ReadingPosition.book_id == book_id
        )
    )
    await session.execute(
        delete(ChapterCompletion).where(
            ChapterCompletion.user_id == user.id,
            ChapterCompletion.chapter_id.in_([ch.id for ch in book.chapters]),
        )
    )
    await session.commit()
    return Response(status_code=204)
