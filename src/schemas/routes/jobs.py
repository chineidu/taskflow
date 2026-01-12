from pydantic import ConfigDict, Field, field_validator

from src import create_logger
from src.schemas.base import BaseSchema
from src.schemas.db.models import TaskModelSchema
from src.schemas.rabbitmq.base import RabbitMQPayload
from src.schemas.types import TaskStatusEnum

logger = create_logger(name="schemas.jobs")


class InputSchema(BaseSchema):
    """Schema for job submission input."""

    task_type: str | None = Field(default=None, description="The type of the job to be processed.")
    queue_name: str | None = Field(default=None, description="The name of the queue to submit the job to.")
    data: list[RabbitMQPayload] = Field(
        default_factory=list, description="List of payloads for the job submission."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "task_type": "data_processing",
                "queue_name": "task_queue",
                "data": [
                    {"payload": {"key1": "value1", "key2": "value2"}},
                    {"payload": {"keyA": "valueA", "keyB": "valueB"}},
                    {"payload": {"keyX": "valueX", "keyY": "valueY"}},
                    {"payload": {"keyM": "valueM", "keyN": "valueN", "keyO": "valueO"}},
                ],
            }
        }
    )


class JobSubmissionResponseSchema(BaseSchema):
    """Schema for job submission response."""

    task_ids: list[str] = Field(description="The unique identifiers for the submitted jobs.")
    number_of_messages: int = Field(default=0, description="The number of messages submitted for the job.")
    status: TaskStatusEnum = Field(
        default=TaskStatusEnum.PENDING, description="The status of the job submission."
    )
    message: str | None = Field(
        default=None, description="Optional message providing additional information."
    )

    @field_validator("status", mode="after")
    @classmethod
    def validate_status(cls, v: TaskStatusEnum | str) -> str:
        """Convert TaskStatusEnum to its string representation."""
        if isinstance(v, TaskStatusEnum):
            return v.value

        if isinstance(v, str):
            return v

        return TaskStatusEnum.PENDING.value


class TasksResponseSchema(BaseSchema):
    """Schema for tasks response."""

    total: int = Field(default=0, description="Total number of tasks matching the criteria.")
    limit: int = Field(default=10, description="Number of tasks per page.")
    offset: int = Field(default=0, description="Number of tasks to skip.")
    tasks: list[TaskModelSchema] = Field(default_factory=list, description="List of tasks.")
