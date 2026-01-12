"""API routes for job submission and retrieval."""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio.session import AsyncSession

from src import create_logger
from src.api.core.dependencies import get_producer
from src.api.core.ratelimit import limiter
from src.api.core.responses import MsgSpecJSONResponse
from src.config import app_config
from src.db.models import aget_db
from src.db.repositories.task_repository import TaskRepository
from src.rabbitmq.utilities import aget_system_health
from src.schemas.routes.metrics import MetricsResponseSchema

if TYPE_CHECKING:
    from src.rabbitmq.producer import RabbitMQProducer

logger = create_logger(name="routes.metrics")
LIMIT_VALUE: int = app_config.api_config.ratelimit.default_rate
router = APIRouter(tags=["metrics"], default_response_class=MsgSpecJSONResponse)


@router.get("/metrics", status_code=status.HTTP_200_OK)
@limiter.limit(f"{LIMIT_VALUE}/minute")
async def fetch_dashboard_metrics(
    request: Request,  # Required by SlowAPI  # noqa: ARG001
    db: AsyncSession = Depends(aget_db),
    producer: "RabbitMQProducer| None" = Depends(get_producer),
) -> MetricsResponseSchema:
    """Route for fetching dashboard metrics."""
    task_repo = TaskRepository(db=db)

    avg_processing_time = await task_repo.aget_average_processing_time()
    metrics = await task_repo.aget_dashboard_metrics()

    # RabbitMQ health check can run independently
    system_health = await aget_system_health(producer=producer)

    return MetricsResponseSchema(
        avg_processing_time_seconds=avg_processing_time,
        dashboard_metrics=metrics,
        system_health=system_health,
    )
