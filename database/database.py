"""
``database.database`` - async engine + session factory facade.

The rest of the app never touches the engine directly; it only uses
``Database.sessions()`` (an ``async_sessionmaker``) to open short-lived
sessions::

    async with db.sessions() as session:
        ...

Call :meth:`Database.init` once at startup to create tables.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.asyncio.engine import AsyncEngine

from database.models import Base


class Database:
    """Thin wrapper around an async SQLAlchemy engine."""

    def __init__(self, url: str, echo: bool = False) -> None:
        """
        Args:
            url: SQLAlchemy URL, e.g. ``sqlite+aiosqlite:///./bot.db``.
            echo: Enable SQL echo logging (useful for debugging only).
        """
        # ``create_async_engine`` is lazy - no connection happens here.
        # ``pool_pre_ping`` transparently reconnects when a managed Postgres
        # (Render, Neon, ...) closed an idle connection.
        self._engine: AsyncEngine = create_async_engine(
            url, echo=echo, future=True, pool_pre_ping=True
        )
        # ``expire_on_commit=False`` so ORM objects stay usable after commit
        # (handlers read ``link.id`` / ``link.short_code`` after create).
        self._sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        """Session factory used by middleware, services and the web server."""
        return self._sessions

    @property
    def engine(self) -> AsyncEngine:
        """Raw engine (rarely needed outside tests/migrations)."""
        return self._engine

    async def init(self) -> None:
        """Create all tables if they do not exist yet (idempotent)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose the engine (call on graceful shutdown)."""
        await self._engine.dispose()
