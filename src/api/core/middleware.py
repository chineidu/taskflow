"""Custom middleware for request ID assignment, logging, and error handling."""

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

from src import create_logger
from src.api.core.exceptions import (
    HTTPError,
    UnauthorizedError,
    UnexpectedError,
)
from src.api.core.responses import MsgSpecJSONResponse
from src.schemas.types import ErrorCodeEnum

logger = create_logger(name="middleware")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to add a unique request ID to each incoming request."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Add a unique request ID to the request and response headers."""
        # Check for existing request ID from client
        client_req_id: str | None = request.headers.get("X-Request-ID", None)

        if client_req_id and len(client_req_id) <= 128:
            request_id = client_req_id.strip()
        else:
            # Generate a new UUID if not provided or invalid
            request_id = str(uuid4())

        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log incoming requests and outgoing responses."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Log request and response details."""
        start_time: float = time.perf_counter()

        request_id = getattr(request.state, "request_id", "N/A")
        response: Response = await call_next(request)
        # in milliseconds
        process_time: float = round(((time.perf_counter() - start_time) * 1000), 2)
        response.headers["X-Process-Time-MS"] = str(process_time)

        log: dict[str, Any] = {
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "process_time_ms": process_time,
            "request_id": request_id,
        }

        logger.info(f"{json.dumps(log)}")

        return response


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Middleware to handle exceptions and return standardized error responses."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Catch exceptions and return standardized error responses."""
        try:
            response: Response = await call_next(request)
            return response

        except HTTPError as exc:
            return MsgSpecJSONResponse(
                status_code=exc.status_code,
                content={
                    "status": "error",
                    "error": {"message": exc.message, "code": ErrorCodeEnum.HTTP_ERROR},
                    "request_id": getattr(request.state, "request_id", "N/A"),
                    "path": str(request.url.path),
                },
            )

        except UnauthorizedError as exc:
            headers: dict[Any, Any] | Any = getattr(exc, "headers", {})
            return MsgSpecJSONResponse(
                status_code=exc.status_code,
                headers=headers,
                content={
                    "status": "error",
                    "error": {"message": exc.message, "code": exc.error_code},
                    "request_id": getattr(request.state, "request_id", "N/A"),
                    "path": str(request.url.path),
                },
            )

        except UnexpectedError as exc:
            return MsgSpecJSONResponse(
                status_code=exc.status_code,
                content={
                    "status": "error",
                    "error": {"message": exc.message, "code": exc.error_code},
                    "request_id": getattr(request.state, "request_id", "N/A"),
                    "path": str(request.url.path),
                },
            )

        except Exception as exc:
            logger.exception(f"Unhandled exception in middleware: {exc}")
            return MsgSpecJSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "status": "error",
                    "error": {
                        "message": "An unexpected server error occurred.",
                        "code": ErrorCodeEnum.UNEXPECTED_ERROR,
                    },
                    "request_id": getattr(request.state, "request_id", "N/A"),
                    "path": str(request.url.path),
                },
            )
