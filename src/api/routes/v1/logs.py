"""API routes for accessing task execution logs."""

from typing import TYPE_CHECKING, Annotated

from aiocache.factory import Cache
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio.session import AsyncSession

from src import create_logger
from src.api.core.cache import cached
from src.api.core.dependencies import aget_cache, aget_storage_service
from src.api.core.exceptions import HTTPError, ResourcesNotFoundError
from src.api.core.ratelimit import limiter
from src.api.core.responses import MsgSpecJSONResponse
from src.config import app_config
from src.db.models import aget_db
from src.db.repositories.task_repository import TaskRepository
from src.schemas.routes.logs import LogResponse

if TYPE_CHECKING:
    from src.services.storage import S3StorageService

logger = create_logger("logs_routes")
LIMIT_VALUE: int = app_config.api_config.ratelimit.default_rate
router = APIRouter(tags=["logs"], default_response_class=MsgSpecJSONResponse)


@router.get(
    "/{task_id}/logs",
    response_model=LogResponse,
)
@cached(ttl=300, key_prefix="job")  # type: ignore
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def get_task_logs(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    task_id: Annotated[str, Path(description="The unique task identifier")],
    db: AsyncSession = Depends(aget_db),
    storage_service: "S3StorageService" = Depends(aget_storage_service),
    cache: Cache = Depends(aget_cache),  # Required by caching decorator  # noqa: ARG001
) -> StreamingResponse:
    """Retrieve execution logs for a completed or failed task.

    Parameters
    ----------
    task_id : str
        The unique task identifier.

    Returns
    -------
    StreamingResponse
        The log content.
    """
    logger.info(f"[+] Retrieving logs for task_id={task_id}")
    task_repo = TaskRepository(db)
    _task = await task_repo.aget_task_by_id(task_id)

    if not _task:
        logger.warning(f"[x] Task not found: task_id={task_id}")
        raise ResourcesNotFoundError(f"task '{task_id}'")

    if not _task.has_logs:
        logger.warning(f"[x] Task exists but has no logs (status={_task.status}): task_id={task_id}")
        raise ResourcesNotFoundError(f"logs for task '{task_id}'")

    task = task_repo.convert_dbtask_to_schema(_task)
    if not task:
        logger.warning(f"[x] Task conversion failed: task_id={task_id}")
        raise ResourcesNotFoundError(f"task '{task_id}'")

    # Download log from S3
    try:
        body = await storage_service.aget_s3_stream(task.task_id)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "NoSuchKey":
            logger.error(f"[x] Log object not found in S3 for task_id='{task_id}'")
            raise ResourcesNotFoundError(f"logs for task '{task_id}'") from e
        logger.error(f"[x] S3 error retrieving logs for task_id='{task_id}': {e}")
        raise HTTPError(details=f"Failed to retrieve logs for task '{task_id}'") from e
    except BotoCoreError as e:
        logger.error(f"[x] BotoCore error retrieving logs for task_id='{task_id}': {e}")
        raise HTTPError(details=f"Failed to retrieve logs for task '{task_id}'") from e
    except Exception as e:
        logger.error(f"[x] Unexpected error retrieving logs from storage for task_id='{task_id}': {e}")
        raise HTTPError(details=f"Failed to retrieve logs for task '{task_id}'") from e

    if not body:
        logger.error(f"[x] Failed to retrieve logs from storage for task_id='{task_id}'")
        raise HTTPError(
            details=f"Failed to retrieve logs for task '{task_id}'",
        )

    # Stream it back to the user
    return StreamingResponse(
        storage_service.as3_stream_generator(body),
        media_type="text/plain",
        headers={"Content-Disposition": f"inline; filename={task_id}.log"},
    )
