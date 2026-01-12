"""API routes for job submission and retrieval."""

from typing import TYPE_CHECKING, Annotated, Any

from aiocache.factory import Cache
from fastapi import APIRouter, Depends, Path, Request, status
from fastapi.params import Query
from sqlalchemy.ext.asyncio.session import AsyncSession

from src import create_logger
from src.api.core.cache import cached
from src.api.core.dependencies import get_cache, get_producer, idempotency_key_header, request_id_header_doc
from src.api.core.exceptions import HTTPError, ResourcesNotFoundError
from src.api.core.ratelimit import limiter
from src.api.core.responses import MsgSpecJSONResponse
from src.api.utilities import generate_idempotency_key
from src.config import app_config
from src.db.models import aget_db
from src.db.repositories.task_repository import TaskRepository
from src.schemas.db.models import TaskModelSchema
from src.schemas.rabbitmq.base import SubmittedJobResult
from src.schemas.routes.jobs import (
    InputSchema,
    JobSubmissionResponseSchema,
    TasksResponseSchema,
)
from src.schemas.types import TaskStatusEnum
from src.services.producer import areplay_dlq_message_by_task_id, areplay_dlq_messages, atrigger_job

if TYPE_CHECKING:
    from src.rabbitmq.producer import RabbitMQProducer
    from src.schemas.rabbitmq.base import RabbitMQPayload

logger = create_logger(name="routes.jobs")
LIMIT_VALUE: int = app_config.api_config.ratelimit.default_rate
router = APIRouter(tags=["jobs"], default_response_class=MsgSpecJSONResponse)


# Order of routes matters here due to FastAPI's routing mechanism
# Routes with path parameters should be defined BEFORE more general routes
# OR you can use APIRouter with different prefixes to avoid conflicts.


@router.post("/jobs", status_code=status.HTTP_200_OK)
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def submit_job(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    data: InputSchema,
    _=Depends(request_id_header_doc),  # noqa: ANN001
    idem_key: str | None = Depends(idempotency_key_header),
    db: AsyncSession = Depends(aget_db),
    producer: "RabbitMQProducer| None" = Depends(get_producer),
) -> JobSubmissionResponseSchema:
    """Route for submitting job for processing."""
    request_id: str = request.headers.get("X-Request-ID", "")
    task_repo = TaskRepository(db=db)
    messages: list["RabbitMQPayload"] = data.data
    queue_name: str = data.queue_name or "default_queue"

    idempotency_key: str = (
        idem_key if idem_key else generate_idempotency_key(payload=data.model_dump(), user_id=None)
    )
    print(f"[ ] Using idempotency_key={idempotency_key} for job submission")

    # Check for existing tasks with the same idempotency key
    existing_tasks = await task_repo.aget_tasks_by_idempotency_key(f"{idempotency_key}_")

    # Append suffix to differentiate multiple messages
    if existing_tasks and all(
        task.idempotency_key.startswith(f"{idempotency_key}_") for task in existing_tasks
    ):
        logger.info(f"[x] Duplicate job submission detected with idempotency_key={idempotency_key}")
        return JobSubmissionResponseSchema(
            task_ids=[task.task_id for task in existing_tasks],
            number_of_messages=len(existing_tasks),
            status=existing_tasks[0].status,
            message="Duplicate job submission detected. Returning all existing tasks in the batch.",
        )

    headers: dict[str, str] = {"Idempotency-Key": idempotency_key}

    response: SubmittedJobResult = await atrigger_job(
        messages=messages, queue_name=queue_name, request_id=request_id, producer=producer, headers=headers
    )
    if not response:
        raise HTTPError(
            details="Job submission failed",
        )

    # Store tasks in the database
    tasks: list[TaskModelSchema] = [
        TaskModelSchema(
            task_id=response.task_ids[i] if i < len(response.task_ids) else "unknown",
            payload={"message": message, "queue_name": queue_name},
            status=TaskStatusEnum.PENDING,
            has_logs=False,
            log_s3_key=None,
            log_s3_url=None,
            idempotency_key=f"{idempotency_key}_{i}",
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
        message="Job submitted successfully.",
    )


@router.get("/jobs", status_code=status.HTTP_200_OK)
@cached(ttl=300, key_prefix="job")  # type: ignore
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def get_all_jobs(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    status: Annotated[TaskStatusEnum | None, Query(description="Filter by task status")] = None,
    limit: Annotated[int, Query(description="Number of items per page", ge=1, le=100)] = 10,
    offset: Annotated[int, Query(description="Number of items to skip", ge=0)] = 0,
    db: AsyncSession = Depends(aget_db),
    cache: Cache = Depends(get_cache),  # Required by caching decorator  # noqa: ARG001
) -> TasksResponseSchema:
    """Route for fetching all tasks with pagination and optional status filtering."""
    task_repo = TaskRepository(db=db)
    # Use database-level pagination for better performance
    _tasks, total = await task_repo.aget_tasks_paginated(
        status=status,
        limit=limit,
        offset=offset,
    )

    tasks = [task_repo.convert_dbtask_to_schema(task) for task in _tasks]
    tasks = [task for task in tasks if task is not None]

    if not tasks:
        logger.warning("[x] No tasks found matching the criteria")
        raise ResourcesNotFoundError("tasks matching the criteria")

    return TasksResponseSchema(
        tasks=tasks,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/jobs/action/retry", status_code=status.HTTP_200_OK)
@cached(ttl=300, key_prefix="job")  # type: ignore
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def retry_batch_jobs(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    target_queue_name: Annotated[
        str, Query(description="The target queue name to replay the message to")
    ] = "task_queue",
    max_messages: Annotated[int, Query(description="Maximum number of messages to search through")] = 1000,
) -> dict[str, Any]:
    """Route for retrying batch jobs from the Dead Letter Queue (DLQ) by task ID."""
    # Use database-level pagination for better performance
    result = await areplay_dlq_messages(
        dlq_name="task_queue_dlq",
        target_queue_name=target_queue_name,
        max_messages=max_messages,
    )

    if not result.get("success", None):
        logger.warning("[x] Failed to replay batch messages from DLQ")
        raise ResourcesNotFoundError("batch messages in DLQ or replay failed")

    return result


@router.post("/jobs/action/{task_id}/retry", status_code=status.HTTP_200_OK)
@cached(ttl=300, key_prefix="job")  # type: ignore
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def retry_job(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    task_id: Annotated[str, Path(description="The ID of the task to retry.")],
    target_queue_name: Annotated[
        str, Query(description="The target queue name to replay the message to")
    ] = "task_queue",
    max_messages: Annotated[int, Query(description="Maximum number of messages to search through")] = 1000,
) -> dict[str, Any]:
    """Route for retrying a job from the Dead Letter Queue (DLQ) by task ID."""
    result = await areplay_dlq_message_by_task_id(
        dlq_name="task_queue_dlq",
        target_queue_name=target_queue_name,
        task_id=task_id,
        max_messages=max_messages,
    )

    if not result.get("success", False):
        logger.warning(f"[x] Failed to replay message with task_id={task_id}")
        raise ResourcesNotFoundError(f"task '{task_id}' in DLQ or replay failed")

    return result


@router.get("/jobs/{task_id}", status_code=status.HTTP_200_OK)
@cached(ttl=300, key_prefix="job")  # type: ignore
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def get_job(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    task_id: Annotated[str, Path(description="The ID of the task to retrieve.")],
    db: AsyncSession = Depends(aget_db),
    cache: Cache = Depends(get_cache),  # Required by caching decorator  # noqa: ARG001
) -> TaskModelSchema:
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
