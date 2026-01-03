from typing import TYPE_CHECKING, Annotated

from aiocache.factory import Cache
from fastapi import APIRouter, Depends, Path, Request, status
from fastapi.params import Query
from sqlalchemy.ext.asyncio.session import AsyncSession

from src import create_logger
from src.api.core.cache import cached
from src.api.core.dependencies import aget_cache, arequest_id_header_doc
from src.api.core.exceptions import HTTPError, ResourcesNotFoundError
from src.api.core.ratelimit import limiter
from src.api.core.responses import MsgSpecJSONResponse
from src.config import app_config
from src.db.models import aget_db
from src.db.repositories.task_repository import TaskRepository
from src.schemas.db.models import TaskModel
from src.schemas.rabbitmq.payload import SubmittedJobResult
from src.schemas.routes.job import InputSchema, JobSubmissionResponseSchema, TasksResponse
from src.schemas.types import TaskStatusEnum
from src.services.producer import atrigger_job

if TYPE_CHECKING:
    from src.schemas.rabbitmq.payload import RabbitMQPayload

logger = create_logger(name="routes.submit_job")
LIMIT_VALUE: int = app_config.api_config.ratelimit.default_rate
router = APIRouter(tags=["submit_job"], default_response_class=MsgSpecJSONResponse)


@router.post("/jobs", status_code=status.HTTP_200_OK)
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def submit_job(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    data: InputSchema,
    _=Depends(arequest_id_header_doc),  # noqa: ANN001
    db: AsyncSession = Depends(aget_db),
) -> JobSubmissionResponseSchema:
    """Route for submitting job for processing."""
    request_id: str = request.headers.get("X-Request-ID", "")
    task_repo = TaskRepository(db=db)
    messages: list["RabbitMQPayload"] = data.data
    queue_name: str = data.queue_name or "default_queue"

    response: SubmittedJobResult = await atrigger_job(
        messages=messages, queue_name=queue_name, request_id=request_id
    )
    if not response:
        raise HTTPError(
            details="Job submission failed",
        )

    # Store tasks in the database
    tasks: list[TaskModel] = [
        TaskModel(
            task_id=response.task_ids[i] if i < len(response.task_ids) else "unknown",
            payload={"message": message, "queue_name": queue_name},
            status=TaskStatusEnum.PENDING,
            has_logs=False,
            log_s3_key=None,
            log_s3_url=None,
        )
        for i, message in enumerate(messages)
    ]
    if not tasks:
        logger.error("[x] No tasks to store in the database after job submission")
        raise HTTPError(
            details="No tasks to store in the database",
        )
    await task_repo.acreate_tasks(tasks=tasks)

    return JobSubmissionResponseSchema(
        task_ids=response.task_ids,
        number_of_messages=response.number_of_messages,
        status=TaskStatusEnum.PENDING,
    )


@router.get("/jobs/{task_id}", status_code=status.HTTP_200_OK)
@cached(ttl=300, key_prefix="job")  # type: ignore
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def get_job(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    task_id: Annotated[str, Path(description="The ID of the task to retrieve.")],
    db: AsyncSession = Depends(aget_db),
    cache: Cache = Depends(aget_cache),  # Required by caching decorator  # noqa: ARG001
) -> TaskModel:
    """Route for fetching task details."""
    task_repo = TaskRepository(db=db)
    _task = await task_repo.aget_task_by_id(task_id)

    if not _task:
        logger.warning(f"[x] Task not found: task_id={task_id}")
        raise ResourcesNotFoundError(f"task '{task_id}'")

    task = task_repo.convert_dbtask_to_schema(_task)
    if not task:
        logger.warning(f"[x] Task conversion failed: task_id={task_id}")
        raise ResourcesNotFoundError(f"task '{task_id}'")

    return task


@router.get("/jobs", status_code=status.HTTP_200_OK)
@cached(ttl=300, key_prefix="job")  # type: ignore
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def get_all_jobs(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    status: Annotated[TaskStatusEnum | None, Query(description="Filter by task status")] = None,
    limit: Annotated[int, Query(description="Number of items per page", ge=1, le=100)] = 10,
    offset: Annotated[int, Query(description="Number of items to skip", ge=0)] = 0,
    db: AsyncSession = Depends(aget_db),
    cache: Cache = Depends(aget_cache),  # Required by caching decorator  # noqa: ARG001
) -> TasksResponse:
    """Route for fetching all tasks with pagination and optional status filtering."""
    task_repo = TaskRepository(db=db)
    # Use database-level pagination for better performance
    _tasks, total = await task_repo.aget_tasks_paginated(
        status=status,
        limit=limit,
        offset=offset,
    )

    tasks = [task_repo.convert_dbtask_to_schema(task) for task in _tasks]

    if not tasks:
        logger.warning("[x] No tasks found matching the criteria")
        raise ResourcesNotFoundError("tasks matching the criteria")

    return TasksResponse(
        tasks=tasks,
        total=total,
        limit=limit,
        offset=offset,
    )
