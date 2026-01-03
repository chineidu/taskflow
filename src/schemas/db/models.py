from dataclasses import asdict, dataclass, field
from typing import Any

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
        metadata={"description": "S3 key where the task logs are stored."},
    )
    log_s3_url: str | None = field(
        metadata={"description": "S3 URL where the task logs are stored."},
    )
    created_at: str | None = field(
        default=None, metadata={"description": "Timestamp when the task was created."}
    )
    started_at: str | None = field(
        default=None, metadata={"description": "Timestamp when the task started."},
    )
    completed_at: str | None = field(
        default=None, metadata={"description": "Timestamp when the task was completed."},
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
