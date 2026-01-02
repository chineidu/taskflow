"""Database models and utilities."""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import app_settings
from src.db import AsyncDatabasePool


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


# =========================================================
# ==================== Database Models ====================
# =========================================================


class DBTask(Base):
    """Data model for storing task information."""

    __tablename__: str = "tasks"

    id: Mapped[int] = mapped_column("id", primary_key=True)
    task_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Index for faster queries on `status` and `created_at` fields
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    has_logs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    log_s3_key: Mapped[str] = mapped_column(String(255), nullable=True)
    log_s3_url: Mapped[str] = mapped_column(Text, nullable=True)

    # Composite index on status and created_at for optimized queries
    __table_args__ = (Index("ix_tasks_status_created_at", "status", "created_at"),)

    def __repr__(self) -> str:
        """
        Returns a string representation of the Task object.

        Returns
        -------
        str
        """
        return f"{self.__class__.__name__}(id={self.id!r}, task_id={self.task_id!r}, status={self.status!r})"


# =========================================================
# ==================== Utilities ==========================
# =========================================================
# Global pool instance
_db_pool: AsyncDatabasePool | None = None


async def aget_db_pool() -> AsyncDatabasePool:
    """Get or create the global async database pool."""
    global _db_pool
    if _db_pool is None:
        _db_pool = AsyncDatabasePool(app_settings.database_url)
    return _db_pool


@asynccontextmanager
async def aget_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session context manager.

    Use this for manual session management with 'with' statements.

    Yields
    ------
    Session
        A database session

    Example
    -------
        with aget_db_session() as session:
            # use session here
    """
    db_pool = await aget_db_pool()
    async with db_pool.aget_session() as session:
        yield session


async def aget_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for async database sessions.

    This is a generator function that FastAPI will handle automatically.
    Use this with Depends() in your route handlers.

    Yields
    ------
    Session
        An async database session that will be automatically closed after the request
    """
    db_pool = await aget_db_pool()
    async with db_pool.aget_session() as session:
        try:
            yield session
        finally:
            # Session cleanup is handled by the context manager
            pass
