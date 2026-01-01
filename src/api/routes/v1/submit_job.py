from aiocache import Cache
from fastapi import APIRouter, Depends, Request, status

from src import create_logger
from src.api.core.cache import cached
from src.api.core.dependencies import get_cache, request_id_header_doc
from src.api.core.exceptions import HTTPError, UnexpectedError
from src.api.core.ratelimit import limiter
from src.api.core.responses import MsgSpecJSONResponse
from src.config import app_config
from src.schemas.rabbitmq.payload import SubmittedJobResult
from src.schemas.routes.sumbit_job import InputSchema, JobSubmissionResponseSchema
from src.schemas.types import TaskStatusEnum
from src.services.producer import atrigger_job

logger = create_logger(name="routes.submit_job")
LIMIT_VALUE: int = app_config.api_config.ratelimit.default_rate
router = APIRouter(tags=["submit_job"], default_response_class=MsgSpecJSONResponse)


@router.post("/jobs", status_code=status.HTTP_200_OK)
@cached(ttl=300, key_prefix="jobs")  # type: ignore
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def submit_job(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    data: InputSchema,
    _=Depends(request_id_header_doc),  # noqa: ANN001
    cache: Cache = Depends(get_cache),  # Required by caching decorator  # noqa: ARG001
) -> JobSubmissionResponseSchema:
    """Route for submitting job for processing."""
    request_id: str = request.headers.get("X-Request-ID", "")
    try:
        messages = data.data
        queue_name = data.queue_name or "default_queue"
        response: SubmittedJobResult = await atrigger_job(
            messages=messages, queue_name=queue_name, request_id=request_id
        )

        if not response:
            raise HTTPError(
                details="Job submission failed",
            )

        return JobSubmissionResponseSchema(
            task_ids=response.task_ids,
            number_of_messages=response.number_of_messages,
            status=TaskStatusEnum.PENDING,
        )

    except Exception as e:
        logger.error(f"Unexpected error during job submission: {e}")
        raise UnexpectedError(
            details="Unexpected error occurred while submitting job."
        ) from e
