"""Integration Tests for API Routes."""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DBTask
from src.schemas.db.models import TaskModel
from src.schemas.rabbitmq.payload import SubmittedJobResult
from src.schemas.types import TaskStatusEnum


@pytest.mark.asyncio
async def test_health_check(client: TestClient) -> None:
    """Test the health check endpoint."""
    response = client.get("/api/v1/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
class TestClientJobRoutes:
    """Tests for job-related API routes."""

    async def test_submit_job(self, client: TestClient, db_session: AsyncSession) -> None:  # noqa: ARG001, ARG002
        """Test job submission endpoint."""
        # Mock the trigger_job service to avoid RabbitMQ interaction
        with patch("src.api.routes.v1.jobs.atrigger_job", new_callable=AsyncMock) as mock_trigger:
            mock_trigger.return_value = SubmittedJobResult(task_ids=["task-123"], number_of_messages=1)

            payload = {"data": [{"payload": {"key": "value"}}], "queue_name": "test_queue"}

            response = client.post("/api/v1/jobs", json=payload)

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["taskIds"] == ["task-123"]
            assert data["status"] == TaskStatusEnum.PENDING.value

            # Verify mock was called
            mock_trigger.assert_called_once()

    async def test_get_job_status(self, client: TestClient, db_session: AsyncSession) -> None:
        """Test retrieving job status."""
        # Insert a task
        task_id = "status-test-task"
        task = DBTask(
            **TaskModel(
                task_id=task_id,
                payload={"data": "test"},
                status=TaskStatusEnum.IN_PROGRESS,
                has_logs=False,
            ).model_dump()
        )
        db_session.add(task)
        await db_session.commit()

        response = client.get(f"/api/v1/jobs/{task_id}")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["taskId"] == task_id
        assert data["status"] == TaskStatusEnum.IN_PROGRESS.value

    async def test_get_job_not_found(self, client: TestClient) -> None:
        """Test retrieving a non-existent job."""
        response = client.get("/api/v1/jobs/nonexistent")
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
class TestClientLogRoutes:
    """Tests for log-related API routes."""

    async def test_get_task_logs(self, client: TestClient, db_session: AsyncSession) -> None:
        """Test retrieving task logs."""
        # Insert a task with logs
        task_id = "log-test-task"
        task = DBTask(
            **TaskModel(
                task_id=task_id,
                payload={"data": "test"},
                status=TaskStatusEnum.COMPLETED,
                has_logs=True,
                log_s3_key="logs/test.log",
                log_s3_url="s3://bucket/logs/test.log",
            ).model_dump()
        )
        db_session.add(task)
        await db_session.commit()

        # Mock S3 storage service
        # The dependency is `aget_storage_service`. We need to override it or patch it.
        # Since `aget_storage_service` returns a service instance, we can patch the dependency.

        mock_storage = MagicMock()
        mock_stream = MagicMock()

        # read returns chunks
        mock_stream.read.side_effect = [b"log line 1\n", b"log line 2\n", b""]

        # aget_s3_stream is async, so it should return an awaitable that resolves to the stream
        mock_storage.aget_s3_stream = AsyncMock(return_value=mock_stream)

        async def mock_stream_generator(body: bytes) -> AsyncGenerator[bytes, None]:  # noqa: ARG001
            yield b"log line 1\n"
            yield b"log line 2\n"

        mock_storage.as3_stream_generator = mock_stream_generator

        # The `client` fixture creates the app. We can't easily modify it here unless we access it.
        # But `client.app` gives access to the FastAPI app.

        from src.api.core.dependencies import aget_storage_service

        client.app.dependency_overrides[aget_storage_service] = lambda: mock_storage  # type: ignore

        response = client.get(f"/api/v1/{task_id}/logs")

        assert response.status_code == status.HTTP_200_OK
        assert response.content == b"log line 1\nlog line 2\n"

        # Clean up override
        del client.app.dependency_overrides[aget_storage_service]  # type: ignore

    async def test_get_task_logs_no_logs(self, client: TestClient, db_session: AsyncSession) -> None:
        """Test retrieving logs for a task that has no logs."""
        # Insert a task without logs
        task_id = "no-log-task"
        task = DBTask(
            **TaskModel(
                task_id=task_id,
                payload={"data": "test"},
                status=TaskStatusEnum.PENDING,
                has_logs=False,
            ).model_dump()
        )
        db_session.add(task)
        await db_session.commit()

        response = client.get(f"/api/v1/{task_id}/logs")

        assert response.status_code == status.HTTP_404_NOT_FOUND
