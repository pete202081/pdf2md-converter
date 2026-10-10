"""Verify the PostgreSQL search branch emits an indexable, owner-filtered query."""

from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.repositories.search_repository import SearchRepository


async def test_postgres_fts_statement_uses_owner_scope_and_bound_term() -> None:
    class CaptureSession:
        bind = SimpleNamespace(dialect=postgresql.dialect())
        statement = None

        async def scalar(self, statement):
            return 0

        async def scalars(self, statement):
            self.statement = statement
            return SimpleNamespace(all=lambda: [])

    session = CaptureSession()
    docs, pending = await SearchRepository(session).find(2, False, "PRIVATE query", 20)
    assert docs == [] and pending == 0
    assert session.statement is not None
    compiled = session.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "to_tsvector('english'::regconfig, search_documents.content)" in sql
    assert "websearch_to_tsquery" in sql and "@@" in sql and "ts_rank" in sql
    assert "jobs.owner_id" in sql and "jobs.status" in sql and "search_index_state" in sql
    assert "LIMIT" in sql
    assert "PRIVATE query" not in sql
    assert "PRIVATE query" in compiled.params.values()
