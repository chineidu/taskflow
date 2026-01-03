"""Schemas for logs-related API responses."""

from dataclasses import asdict, dataclass
from typing import Any

from src.schemas.base import BaseSchema


@dataclass(slots=True, kw_only=True)
class S3UploadMetadata:
    task_id: str
    uploaded_at: str
    correlation_id: str
    environment: str
    service: str = "taskflow"
    log_level: str = "INFO"


@dataclass(slots=True, kw_only=True)
class UploadResultExtraArgs:
    """The extra arguments for S3 upload.

    Note
    ----
    The field names MUST match the expected keys in boto3's `ExtraArgs` parameter.
    """

    Metadata: S3UploadMetadata
    # AccessControlList
    ACL: str
    ContentType: str

    def model_dump(self) -> dict[str, Any]:
        """Convert to a dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary representation.
        """
        return asdict(self)


class LogMetadata(BaseSchema):
    """Metadata about a task's execution logs."""

    task_id: str
    has_logs: bool
    log_size_bytes: int | None = None
    s3_url: str | None = None


class LogResponse(BaseSchema):
    """Response containing log content."""

    task_id: str
    log_content: str
    retrieved_at: str


class LogNotFoundResponse(BaseSchema):
    """Response when logs are not found."""

    detail: str
    task_id: str


class LogOversizedResponse(BaseSchema):
    """Response when log file exceeds size limit."""

    detail: str
    task_id: str
    max_size_bytes: int
