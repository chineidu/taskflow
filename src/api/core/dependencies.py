import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from aiocache import Cache
from fastapi import Header, Request

from src.api.core.exceptions import ResourcesNotFoundError
from src.schemas.types import ResourceEnum

if TYPE_CHECKING:
    pass


_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


async def get_cache(request: Request) -> Cache:
    """Dependency to inject cache into endpoints."""
    if not hasattr(request.app.state, "cache") or request.app.state.cache is None:
        raise ResourcesNotFoundError(resource_type=ResourceEnum.CACHE)
    return request.app.state.cache


async def request_id_header_doc(
    x_request_id: str | None = Header(  # noqa: ARG001
        default=None,
        alias="X-Request-ID",
        description="Optional request ID. If provided, it will be reused; otherwise generated.",
        example="my-trace-001",
    ),
) -> None:
    """Dependency to document the X-Request-ID header in OpenAPI."""
    return


def get_executor(max_workers: int | None = None) -> ThreadPoolExecutor:
    """Return a shared global ThreadPoolExecutor.

    Sharing a single executor across all instances ensures:
    1. Efficient use of system resources (avoids creating a new executor per request).
    2. Proper timeout and cancellation handling with asyncio.
    3. Thread reuse for better performance under concurrent predictions.
    """
    global _executor
    global _executor_lock

    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="global_threadpool_",
                )

    #  _executor is guaranteed to be initialized
    assert _executor is not None
    return _executor
