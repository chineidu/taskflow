from datetime import datetime
from typing import Any

from pydantic import Field

from src.schemas.base import BaseSchema
from src.schemas.types import TaskStatusEnum


class TaskModelSchema(BaseSchema):
    """Pydantic schema for TaskModel (for API Response)."""

    id: int | None = Field(
        default=None,
        description="Unique identifier for the task in the database.",
    )
    task_id: str = Field(description="Unique task identifier.")
    payload: dict[str, Any] = Field(description="Payload data associated with the task.")
    status: TaskStatusEnum = Field(description="Current status of the task.")
    has_logs: bool = Field(
        description="Indicates if the task has associated logs.",
    )
    log_s3_key: str | None = Field(
        default=None,
        description="S3 key where the task logs are stored.",
    )
    log_s3_url: str | None = Field(
        default=None,
        description="S3 URL where the task logs are stored.",
    )
    in_dlq: bool = Field(
        default=False,
        description="Indicates if the task is currently in the Dead Letter Queue (DLQ).",
    )
    created_at: datetime | None = Field(default=None, description="Timestamp when the task was created.")
    started_at: datetime | None = Field(
        default=None,
        description="Timestamp when the task started.",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp when the task was completed.",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="Timestamp when the task was last updated.",
    )
    error_message: str | None = Field(default=None, description="Error message if the task failed.")
