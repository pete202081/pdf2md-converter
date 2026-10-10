"""SQL adapter for private, bounded full-text book lookup."""

from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Job, SearchDocument
from app.domain.enums import JobStatus


class SearchRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find(
        self, user_id: int, is_admin: bool, query: str, limit: int
    ) -> tuple[list[SearchDocument], int]:
        """Ownership, state and match are enforced in SQL, before ranking and LIMIT."""
        scope: list[ColumnElement[bool]] = [
            Job.status.in_((JobStatus.SUCCEEDED, JobStatus.PARTIAL))
        ]
        if not is_admin:
            scope.append(Job.owner_id == user_id)
        pending = await self.session.scalar(
            select(func.count()).select_from(Job).where(*scope, Job.search_index_state != "READY")
        )
        if self.session.bind and self.session.bind.dialect.name == "postgresql":
            # Must match the expression in the PostgreSQL GIN migration verbatim.
            english: ColumnElement[Any] = literal_column("'english'::regconfig")
            vector = func.to_tsvector(english, SearchDocument.content)
            tsquery = func.websearch_to_tsquery(english, query)
            matching = vector.op("@@")(tsquery)
            rank = func.ts_rank(vector, tsquery)
            ordering: tuple[Any, ...] = (
                rank.desc(),
                Job.created_at.desc(),
                SearchDocument.chapter_order,
            )
        else:
            # Bounded SQLite literal-match fallback for isolated tests only.
            pattern = (
                "%"
                + query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            matching = func.lower(SearchDocument.content).like(pattern, escape="\\")
            ordering = (Job.created_at.desc(), SearchDocument.chapter_order)
        statement = (
            select(SearchDocument)
            .join(Job, SearchDocument.job_id == Job.id)
            .where(*scope, Job.search_index_state == "READY", matching)
            .order_by(*ordering)
            .limit(limit)
        )
        rows = (await self.session.scalars(statement)).all()
        return list(rows), pending or 0
