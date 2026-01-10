"""
Test configuration and fixtures for the application.
"""

from typing import TYPE_CHECKING, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.db.models import Base

if TYPE_CHECKING:
    pass


# ==========================================================
# ======================== CLIENT  =========================
# ==========================================================
@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[TestClient, None]:
    """Create a TestClient for FastAPI app."""
    # Import here to avoid circular imports

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from src.api.core.dependencies import get_cache
    from src.api.core.exceptions import BaseAPIError, api_error_handler
    from src.api.routes import health, jobs, logs
    from src.config import app_config
    from src.db.models import aget_db

    # Create a test app without lifespan to avoid database connections
    prefix = app_config.api_config.prefix
    test_app = FastAPI(
        title="Test API",
        description="API for testing",
        version="1.0.0",
        docs_url=None,  # Disable docs for tests
        redoc_url=None,
        lifespan=None,  # No lifespan for tests
    )

    # Configure CORS middleware (same as production)
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=app_config.api_config.middleware.cors.allow_origins,
        allow_credentials=app_config.api_config.middleware.cors.allow_credentials,
        allow_methods=app_config.api_config.middleware.cors.allow_methods,
        allow_headers=app_config.api_config.middleware.cors.allow_headers,
    )

    # Add exception handlers
    test_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore
    test_app.add_exception_handler(BaseAPIError, api_error_handler)  # type: ignore

    # Override database dependency
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    # Override cache dependency
    async def override_get_cache() -> MagicMock:
        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()
        return mock_cache

    test_app.dependency_overrides[aget_db] = override_get_db
    test_app.dependency_overrides[get_cache] = override_get_cache

    # Include routers
    test_app.include_router(health.router, prefix=prefix)
    test_app.include_router(jobs.router, prefix=prefix)
    test_app.include_router(logs.router, prefix=prefix)

    with TestClient(test_app) as test_client:
        yield test_client


# ==========================================================
# ================== ASYNC DATABASE SETUP ==================
# ==========================================================
@pytest_asyncio.fixture(
    # Each test gets a fresh in-memory database/instance
    scope="function",
)
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create a test database engine using SQLite."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={
            # Allow usage of the same connection in different threads
            "check_same_thread": False,
        },
        poolclass=StaticPool,
        echo=False,
    )
    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def tables(engine: AsyncEngine) -> AsyncGenerator[None, None]:
    """Create all database tables.

    It uses the engine fixture to get the test database engine.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ==========================================================
# ======================== MOCKS ===========================
# ==========================================================
@pytest.fixture(scope="function")
def mock_config() -> MagicMock:
    """Mock application configuration.

    Returns
    -------
    MagicMock
        Mocked configuration object.
    """
    return MagicMock()


@pytest_asyncio.fixture(scope="function")
async def db_session(engine: AsyncEngine, tables: None) -> AsyncGenerator[AsyncSession, None]:  # noqa: ARG001
    """Create a test database session with transaction rollback."""
    async_session = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with async_session() as session:
        yield session
        await session.rollback()
