import time
import warnings
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator

from fastapi import FastAPI

from src import create_logger
from src.api.core.cache import setup_cache
from src.api.core.ratelimit import limiter
from src.config import app_settings
from src.db.init import ainit_db

if TYPE_CHECKING:
    pass
warnings.filterwarnings("ignore")

logger = create_logger(name="api_lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """Initialize and cleanup FastAPI application lifecycle.

    This context manager handles the initialization of required resources
    during startup and cleanup during shutdown.
    """
    try:
        start_time: float = time.perf_counter()
        logger.info(f"ENVIRONMENT: {app_settings.ENVIRONMENT} | DEBUG: {app_settings.DEBUG} ")
        logger.info("Starting up application and loading model...")

        # ====================================================
        # ================= Load Dependencies ================
        # ====================================================

        # ---------- Setup database ----------
        try:
            await ainit_db()
            app.state.db_available = True
            logger.info("✅ Database initialized successfully")
        except Exception as db_error:
            app.state.db_available = False
            logger.warning(f"⚠️  Database unavailable: {db_error}")
            logger.warning("Server will start in degraded mode without database connectivity")

        # ---------- Setup cache ----------
        try:
            app.state.cache = setup_cache()
            # Test Redis connection
            await app.state.cache.set("_healthcheck", "ok", ttl=5)
            await app.state.cache.delete("_healthcheck")
            logger.info("✅ Redis cache initialized successfully")
        except Exception as cache_error:
            logger.error("❌ Redis connection failed")
            logger.error(f"   Error: {cache_error}")
            logger.error(f"   Redis host => {app_settings.REDIS_HOST}:{app_settings.REDIS_PORT}")
            logger.error("   Make sure Redis is running")
            raise RuntimeError(f"Redis is required but unavailable: {cache_error}") from cache_error

        # ---------- Setup rate limiter ----------
        app.state.limiter = limiter
        logger.info("✅ Rate limiter initialized")

        logger.info(f"Application startup completed in {time.perf_counter() - start_time:.2f} seconds")

        # Yield control to the application
        yield

    # ====================================================
    # =============== Cleanup Dependencies ===============
    # ====================================================
    except Exception as e:
        logger.error("❌ Application startup failed")
        logger.error(f"   Reason: {e}")
        raise

    finally:
        logger.info("Shutting down application...")

        # ---------- Cleanup rate limiter ----------
        if hasattr(app.state, "limiter"):
            try:
                app.state.limiter = None
                logger.info("🚨 Rate limiter shutdown.")

            except Exception as e:
                logger.error(f"❌ Error shutting down the rate limiter: {e}")

        # ---------- Cleanup cache ----------
        if hasattr(app.state, "cache"):
            try:
                app.state.cache = None
                logger.info("🚨 Cache shutdown.")

            except Exception as e:
                logger.error(f"❌ Error shutting down the cache: {e}")
