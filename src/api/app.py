import sys
import warnings

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.api.core.lifespan import lifespan
from src.api.core.middleware import (
    ErrorHandlingMiddleware,
    LoggingMiddleware,
    RequestIDMiddleware,
)
from src.api.routes import (
    # admin,
    # auth,
    health,
    # task_status,
    submit_job,
)
from src.config import app_config, app_settings

warnings.filterwarnings("ignore")


def create_application() -> FastAPI:
    """Create and configure a FastAPI application instance.

    This function initializes a FastAPI application with custom configuration settings,
    adds CORS middleware, and includes API route handlers.

    Returns
    -------
    FastAPI
        A configured FastAPI application instance.
    """
    prefix: str = app_config.api_config.prefix
    # auth_prefix: str = app_config.api_config.auth_prefix

    app = FastAPI(
        title=app_config.api_config.title,
        description=app_config.api_config.description,
        version=app_config.api_config.version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Configure CORS middleware
    app.add_middleware(
        CORSMiddleware,  # type: ignore
        allow_origins=app_config.api_config.middleware.cors.allow_origins,
        allow_credentials=app_config.api_config.middleware.cors.allow_credentials,
        allow_methods=app_config.api_config.middleware.cors.allow_methods,
        allow_headers=app_config.api_config.middleware.cors.allow_headers,
    )

    # Add custom middleware
    app.add_middleware(ErrorHandlingMiddleware)  # type: ignore
    app.add_middleware(LoggingMiddleware)  # type: ignore
    app.add_middleware(RequestIDMiddleware)  # type: ignore

    # Include routers
    # app.include_router(admin.router, prefix=prefix)
    # app.include_router(auth.router, prefix=auth_prefix)
    app.include_router(health.router, prefix=prefix)
    app.include_router(submit_job.router, prefix=prefix)
    # app.include_router(task_status.router, prefix=prefix)

    # Add exception handlers
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore

    return app


app: FastAPI = create_application()

if __name__ == "__main__":
    try:
        uvicorn.run(
            "src.api.app:app",
            host=app_settings.HOST,
            port=app_settings.PORT,
            workers=None,  # Will be handled by Gunicorn
            reload=app_settings.RELOAD,
            loop="uvloop",  # Use uvloop for better async performance
        )
    except (Exception, KeyboardInterrupt) as e:
        print(f"Error creating application: {e}")
        print("Exiting gracefully...")
        sys.exit(1)
