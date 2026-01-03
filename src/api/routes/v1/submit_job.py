from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio.session import AsyncSession

from src import create_logger
from src.api.core.dependencies import arequest_id_header_doc
from src.api.core.exceptions import HTTPError, UnexpectedError
from src.api.core.ratelimit import limiter
from src.api.core.responses import MsgSpecJSONResponse
from src.config import app_config
from src.db.models import aget_db
from src.db.repositories.task_repository import TaskRepository
from src.schemas.db.models import TaskModel
from src.schemas.rabbitmq.payload import SubmittedJobResult
from src.schemas.routes.job import InputSchema, JobSubmissionResponseSchema
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
    try:
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
            )
            for i, message in enumerate(messages)
        ]
        if not tasks:
            raise HTTPError(
                details="No tasks to store in the database",
            )
        await task_repo.acreate_tasks(tasks=tasks)

        return JobSubmissionResponseSchema(
            task_ids=response.task_ids,
            number_of_messages=response.number_of_messages,
            status=TaskStatusEnum.PENDING,
        )

    except Exception as e:
        logger.error(f"Unexpected error during job submission: {e}")
        raise UnexpectedError(details="Unexpected error occurred while submitting job.") from e
