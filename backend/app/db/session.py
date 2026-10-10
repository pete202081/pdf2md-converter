"""Async database engine / session factory management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import Base


class Database:
    """Owns the async engine and the session factory.

    Alembic migrates production schema before API/worker start. init_models is
    retained for isolated SQLite tests only.
    """

    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_size=10, max_overflow=20)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def init_models(self) -> None:
        """Create all tables if they do not exist yet."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a new async session (caller is responsible for commit/rollback)."""
        async with self.session_factory() as session:
            yield session

    async def close(self) -> None:
        """Dispose of the engine and its connection pool."""
        await self.engine.dispose()
