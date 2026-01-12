from pydantic import Field

from src.schemas.base import BaseSchema
from src.schemas.db.models import DashboardTaskStats
from src.schemas.rabbitmq.base import SystemHealthResult


class MetricsResponseSchema(BaseSchema):
    """Pydantic schema for Metrics API Response."""

    avg_processing_time_seconds: float = Field(
        description="Average processing time in seconds for the last 100 completed tasks."
    )
    dashboard_metrics: DashboardTaskStats = Field(
        description="Dashboard metrics including total tasks, pending, in-progress, and completed tasks."
    )
    system_health: SystemHealthResult = Field(description="System health status information.")
