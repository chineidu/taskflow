from dataclasses import dataclass, field


@dataclass(slots=True, kw_only=True)
class LogStorageUploadResult:
    attempts: int
    error: str | None = field(default=None, metadata={"description": "Error message if upload failed."})
    s3_key: str | None = field(
        default=None, metadata={"description": "S3 key where the task logs are stored."}
    )
    s3_url: str | None = field(
        default=None, metadata={"description": "S3 URL where the task logs are stored."}
    )
