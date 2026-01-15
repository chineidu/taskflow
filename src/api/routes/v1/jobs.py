"""API routes for job submission and retrieval."""

import json
from typing import TYPE_CHECKING, Annotated, Any

import aio_pika
from aiocache.factory import Cache
from fastapi import APIRouter, Depends, Path, Request, WebSocket, status
from fastapi.params import Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio.session import AsyncSession
from starlette.websockets import WebSocketDisconnect

from src import create_logger
from src.api.core.cache import cached
from src.api.core.dependencies import get_cache, get_producer, idempotency_key_header, request_id_header_doc
from src.api.core.exceptions import HTTPError, ResourcesNotFoundError
from src.api.core.ratelimit import limiter
from src.api.core.responses import MsgSpecJSONResponse
from src.api.utilities import generate_idempotency_key
from src.config import app_config
from src.config.settings import app_settings
from src.db.models import aget_db
from src.db.repositories.task_repository import TaskRepository
from src.rabbitmq.base import BaseRabbitMQ
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
templates = Jinja2Templates(directory="src/api/templates")

# Order of routes matters here due to FastAPI's routing mechanism
# Routes with path parameters should be defined BEFORE more general routes
# OR you can use APIRouter with different prefixes to avoid conflicts.
# Also note that POST endpoints should not be cached.


@router.post("/jobs", status_code=status.HTTP_200_OK)
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def submit_job(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    data: InputSchema,
    _=Depends(request_id_header_doc),  # noqa: ANN001
    idem_key: str | None = Depends(idempotency_key_header),
    db: AsyncSession = Depends(aget_db),
    producer: "RabbitMQProducer| None" = Depends(get_producer),
) -> Any:
    """Route for submitting job for processing."""
    request_id: str = request.headers.get("X-Request-ID", "")
    task_repo = TaskRepository(db=db)
    messages: list["RabbitMQPayload"] = data.data
    queue_name: str = data.queue_name or "default_queue"

    idempotency_key: str = (
        idem_key if idem_key else generate_idempotency_key(payload=data.model_dump(), user_id=None)
    )

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
        messages=messages,
        queue_name=queue_name,
        request_id=request_id,
        producer=producer,
        headers=headers,
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

    if request.headers.get("HX-Request"):
        # If the request comes from HTMX, return the fragment immediately
        return templates.TemplateResponse("task_row.html", {"request": request, "task": tasks})

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
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def retry_batch_jobs(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    target_queue_name: Annotated[
        str, Query(description="The target queue name to replay the message to")
    ] = "task_queue",
    max_messages: Annotated[int, Query(description="Maximum number of messages to search through")] = 1000,
) -> dict[str, Any]:
    """Route for retrying batch jobs from the Dead Letter Queue (DLQ) by task ID."""
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


@router.get("/jobs/{task_id}/status-fragment", status_code=status.HTTP_200_OK)
@cached(ttl=300, key_prefix="job")  # type: ignore
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def get_task_status_fragment(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    task_id: Annotated[str, Path(description="The ID of the task to retrieve status fragment for.")],
    db: AsyncSession = Depends(aget_db),
    cache: Cache = Depends(get_cache),  # Required by caching decorator  # noqa: ARG001
) -> HTMLResponse:
    """Route for fetching a lightweight status fragment of a task."""
    task_repo = TaskRepository(db=db)
    _task = await task_repo.aget_task_by_id(task_id)

    if not _task:
        logger.warning(f"[x] Task not found: task_id={task_id}")
        raise ResourcesNotFoundError(f"task '{task_id}'")

    return templates.TemplateResponse(
        "task_row.html",
        {
            "request": request,
            "task": _task,
        },
    )


@router.get("/jobs/view/demo", status_code=status.HTTP_200_OK)
@cached(ttl=300, key_prefix="job")  # type: ignore
async def render_demo_page(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    cache: Cache = Depends(get_cache),  # Required by caching decorator  # noqa: ARG001
) -> HTMLResponse:
    """Serves the main HTMX jobs page."""
    return templates.TemplateResponse("jobs.html", {"request": request})


@router.websocket("/jobs/{task_id}/status")
async def stream_task_updates(websocket: WebSocket, task_id: str) -> None:
    """WebSocket endpoint to stream real-time task status updates."""
    # Accept the incoming WebSocket connection from the client
    await websocket.accept()
    logger.info(f"[+] WebSocket connected for task_id={task_id}")

    # Create RabbitMQ connection
    rmq_object = BaseRabbitMQ(config=app_config, url=app_settings.rabbitmq_url)
    await rmq_object.aconnect()
    if not rmq_object.connection:
        logger.error("[x] RabbitMQ connection failed for WebSocket streaming")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    try:
        channel = await rmq_object.connection.channel()
        # Declare or ensure the topic exchange where progress events are published.
        # We use a topic exchange so we can bind using routing key patterns.
        exchange = await channel.declare_exchange(
            name=app_config.rabbitmq_config.topic_names.progress_topic,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
            auto_delete=False,
        )
        
        # Create a temporary, exclusive queue for this WebSocket connection.
        # Exclusive means the queue is deleted when this consumer disconnects.
        queue = await channel.declare_queue(exclusive=True)

        # Bind the queue to the exchange using a routing key pattern that matches
        # all event types for the specific task id (e.g. task.progress.<id>).
        # Pattern "*.*.{task_id}" assumes routing keys like "task.created.<id>".
        await queue.bind(exchange, routing_key=f"*.*.{task_id}")
        logger.info(f"[+] WebSocket subscribed to task updates: {task_id}")

        # Use the queue iterator to asynchronously consume messages as they arrive.
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                # Use message.process() to properly ack/nack messages automatically.
                # For more control, remove the 'async with message.process()' block.
                # and manually call message.ack() or message.nack() as needed.
                async with message.process():
                    # Decode the message body and send it to the WebSocket client.
                    event_data = json.loads(message.body.decode())
                    await websocket.send_json(event_data)
                    
                    # If the task has reached a terminal state, stop streaming.
                    # This returns from the endpoint, which will trigger cleanup.
                    if event_data.get("status") in ["completed", "failed"]:
                        logger.info(f"[+] Task {event_data.get('status')}: {task_id}")
                        return

    except WebSocketDisconnect:
        logger.info(f"[x] WebSocket disconnected: {task_id}")
    except Exception as e:
        logger.error(f"[x] Unexpected WebSocket error for {task_id}: {e}", exc_info=True)
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        # Cleanup: Close WebSocket and RabbitMQ connection
        await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
        await rmq_object.adisconnect()
