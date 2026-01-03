from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import Field, TypeAdapter

from src.schemas.base import BaseSchema
from src.schemas.types import TaskStatusEnum


@dataclass(slots=True, kw_only=True)
class TaskModel:
    """Task model representing a task in the database."""

    id: int | None = field(
        default=None,
        metadata={"description": "Unique identifier for the task in the database."},
    )
    task_id: str = field(metadata={"description": "Unique task identifier."})
    payload: dict[str, Any] = field(metadata={"description": "Payload data associated with the task."})
    status: TaskStatusEnum = field(metadata={"description": "Current status of the task."})
    has_logs: bool = field(
        metadata={"description": "Indicates if the task has associated logs."},
    )
    log_s3_key: str | None = field(
        default=None,
        metadata={"description": "S3 key where the task logs are stored."},
    )
    log_s3_url: str | None = field(
        default=None,
        metadata={"description": "S3 URL where the task logs are stored."},
    )
    created_at: str | None = field(
        default=None, metadata={"description": "Timestamp when the task was created."}
    )
    started_at: str | None = field(
        default=None,
        metadata={"description": "Timestamp when the task started."},
    )
    completed_at: str | None = field(
        default=None,
        metadata={"description": "Timestamp when the task was completed."},
    )
    updated_at: str | None = field(
        default=None,
        metadata={"description": "Timestamp when the task was last updated."},
    )
    error_message: str | None = field(
        default=None, metadata={"description": "Error message if the task failed."}
    )

    def model_dump(self, exclude: set[str] | None = None) -> dict[str, Any]:
        """
        Convert the TaskModel instance to a dictionary.

        Parameters
        ----------
        exclude : set[str] | None, default=None
            Set of field names to exclude from the dictionary representation.

        Returns
        -------
        dict[str, Any]
            Dictionary representation of the TaskModel.
        """
        data: dict[str, Any] = asdict(self)
        if exclude:
            for key in exclude:
                data.pop(key, None)
        return data


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
    created_at: str | None = Field(default=None, description="Timestamp when the task was created.")
    started_at: str | None = Field(
        default=None,
        description="Timestamp when the task started.",
    )
    completed_at: str | None = Field(
        default=None,
        description="Timestamp when the task was completed.",
    )
    updated_at: str | None = Field(
        default=None,
        description="Timestamp when the task was last updated.",
    )
    error_message: str | None = Field(default=None, description="Error message if the task failed.")


def convert_task_model_to_response(internal_task: TaskModel) -> TaskModelSchema:
    """Used for converting internal TaskModel to TaskModelSchema for API responses.

    Parameters
    ----------
    internal_task : TaskModel
        The internal task model instance.

    Returns
    -------
    TaskModelSchema
        The Pydantic schema representation of the task.
    """
    return TypeAdapter(TaskModelSchema).validate_python(internal_task)
