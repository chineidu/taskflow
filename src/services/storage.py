"""S3/MinIO storage service for managing task execution logs."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from src import create_logger
from src.config import app_settings
from src.schemas.routes.logs import S3UploadMetadata, UploadResultExtraArgs

logger = create_logger("storage")
MAX_ATTEMPTS: int = 3


def _upload_to_s3(
    client: Any, filepath: str | Path, bucket_name: str, object_name: str, extra_args: UploadResultExtraArgs
) -> bool:
    task_id: str = extra_args.Metadata.task_id
    try:
        client.upload_file(  # type: ignore
            str(filepath),
            bucket_name,
            object_name,
            ExtraArgs=extra_args.model_dump(),
        )
        return True

    except ClientError as e:
        logger.error(f"[x] Failed to upload log for task {task_id}: {e}")
        return False

    except BotoCoreError as e:
        logger.error(f"[x] BotoCore error uploading log for task {task_id}: {e}")
        return False

    except Exception as exc:
        logger.error(f"[x] Unexpected error uploading log for task {task_id}: {exc}")
        return False


def _download_from_s3(client: Any, filepath: str | Path, bucket_name: str, object_name: str) -> bool:
    try:
        client.download_file(bucket_name, object_name, filepath)
        return True

    except Exception as exc:
        logger.error(f"[x] Download failed: {exc}")
        return False


class S3StorageService:
    """
    Service for uploading, downloading, and deleting files from S3-compatible storage.

    Supports AWS S3, MinIO and other S3-compatible backends.
    """

    def __init__(self) -> None:
        """Initialize S3 client with settings from configuration."""
        try:
            self.s3_client = boto3.client(
                "s3",
                aws_access_key_id=app_settings.AWS_ACCESS_KEY_ID.get_secret_value(),
                aws_secret_access_key=app_settings.AWS_SECRET_ACCESS_KEY.get_secret_value(),
                region_name=app_settings.AWS_DEFAULT_REGION,
                config=Config(retries={"max_attempts": MAX_ATTEMPTS, "mode": "adaptive"}),
                # ---- Required for MinIO or other S3-compatible services ----
                endpoint_url=app_settings.aws_s3_endpoint_url,
            )
            self.bucket_name = app_settings.AWS_S3_BUCKET
            self.max_size_bytes = app_settings.LOG_MAX_SIZE_BYTES

            logger.info(
                f"[+] {self.__class__.__name__} initialized: endpoint={app_settings.aws_s3_endpoint_url}, "
                f"bucket={self.bucket_name}"
            )
        except Exception as e:
            logger.error(f"[x] Failed to initialize S3StorageService: {e}")
            raise

    async def aupload_file_to_s3(
        self,
        *,
        filepath: str | Path,
        task_id: str,
        correlation_id: str,
        environment: str,
        max_allowed_size_bytes: int = app_settings.LOG_MAX_SIZE_BYTES,
    ) -> bool:
        """Upload a file to S3 asynchronously.

        Parameters
        ----------
        filepath : str | Path
            Path to the file to upload.
        task_id : str
            Unique task identifier.
        correlation_id : str
            Correlation ID for tracing.
        environment : str
            Application environment (e.g., development, production).
        max_allowed_size_bytes : int, optional
            Maximum allowed file size in bytes, by default app_settings.LOG_MAX_SIZE_BYTES

        Returns
        -------
        bool
            True if upload succeeded, False otherwise.

        Raises
        ------
        ValueError
            If the file size exceeds the maximum allowed size.
        RuntimeError
            If the upload fails.
        """
        filepath = Path(filepath) if isinstance(filepath, str) else filepath
        # Check the file size
        file_size = filepath.stat().st_size
        if file_size > max_allowed_size_bytes:
            raise ValueError(f"Log size: {file_size:,} bytes exceeds max: {max_allowed_size_bytes:,} bytes")

        object_name = f"logs/{filepath.name}"
        extra_args = UploadResultExtraArgs(
            Metadata=S3UploadMetadata(
                task_id=task_id,
                uploaded_at=datetime.now(timezone.utc).isoformat(),
                correlation_id=correlation_id,
                environment=environment,
            ),
            ACL="private",
            ContentType="text/plain",
        )
        success = await self._aupload_to_s3(
            filepath,
            self.bucket_name,
            object_name,
            extra_args,
        )
        if not success:
            raise RuntimeError("S3 upload failed")

        if success:
            logger.info(f"[+] Uploaded '{filepath}' to 's3://{self.bucket_name}/{object_name}'")

        return success

    async def adownload_file_to_s3(
        self,
        *,
        filepath: str | Path,
        task_id: str,
    ) -> bool:
        """Download a file to S3 asynchronously.

        Parameters
        ----------
        filepath : str | Path
            Path to the file to download.
        task_id : str
            Unique task identifier.

        Returns
        -------
        bool
            True if download succeeded, False otherwise.

        Raises
        ------
        RuntimeError
            If the download fails.
        """
        filepath = Path(filepath) if isinstance(filepath, str) else filepath
        object_name: str = f"logs/{task_id}.log"
        success: bool = await self._adownload_from_s3(filepath, self.bucket_name, object_name)
        if not success:
            raise RuntimeError("S3 download failed")

        if success:
            logger.info(f"[+] Downloaded and saved to '{filepath.absolute()}'")

        return success

    async def _aupload_to_s3(
        self,
        filepath: str | Path,
        bucket_name: str,
        object_name: str,
        extra_args: UploadResultExtraArgs,
    ) -> bool:
        """Helper function to upload a file to S3 asynchronously."""
        return await asyncio.to_thread(
            _upload_to_s3,
            self.s3_client,
            filepath,
            bucket_name,
            object_name,
            extra_args,
        )

    async def _adownload_from_s3(
        self,
        filepath: str | Path,
        bucket_name: str,
        object_name: str,
    ) -> bool:
        """Helper function to download a file from S3 asynchronously."""
        return await asyncio.to_thread(
            _download_from_s3,
            self.s3_client,
            filepath,
            bucket_name,
            object_name,
        )
