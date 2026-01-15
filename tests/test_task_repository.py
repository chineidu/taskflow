"""Async Tests for TaskRepository."""

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from src.db.models import DBTask
from src.db.repositories.task_repository import TaskRepository
from src.schemas.db.models import TaskModelSchema
from src.schemas.types import TaskStatusEnum


@pytest.mark.asyncio
class TestTaskRepository:
    """Tests for TaskRepository methods."""

    async def test_aget_task_by_id(self, db_session: AsyncSession) -> None:
        """Test retrieving a task by its ID."""
        # Given
        task_repo = TaskRepository(db_session)

        # Create and add a test task
        test_task = DBTask(
            **TaskModelSchema(
                task_id="test123",
                payload={"key": "value"},
                status=TaskStatusEnum.PENDING,
                has_logs=False,
                idempotency_key="test-key",
                log_s3_key=None,
                log_s3_url=None,
            ).model_dump()
        )
        db_session.add(test_task)
        await db_session.commit()

        # Then
        retrieved_task = await task_repo.aget_task_by_id("test123")

        # Assert
        assert retrieved_task is not None
        assert retrieved_task.task_id == "test123"
        assert retrieved_task.payload == {"key": "value"}

    async def test_aget_tasks_by_ids(self, db_session: AsyncSession) -> None:
        """Test retrieving multiple tasks by their IDs."""
        # Given
        task_repo = TaskRepository(db_session)
        task_ids = ["task1", "task2", "task3"]

        for tid in task_ids:
            task = DBTask(
                **TaskModelSchema(
                    task_id=tid,
                    payload={"id": tid},
                    status=TaskStatusEnum.PENDING,
                    has_logs=False,
                    idempotency_key=f"{tid}-key",
                ).model_dump()
            )
            db_session.add(task)
        await db_session.commit()

        # When
        tasks = await task_repo.aget_tasks_by_ids(["task1", "task3"])

        # Then
        assert len(tasks) == 2
        assert {t.task_id for t in tasks} == {"task1", "task3"}

    async def test_aget_tasks_by_status(self, db_session: AsyncSession) -> None:
        """Test retrieving tasks by status."""
        # Given
        task_repo = TaskRepository(db_session)

        tasks_data = [
            ("t1", TaskStatusEnum.PENDING),
            ("t2", TaskStatusEnum.COMPLETED),
            ("t3", TaskStatusEnum.PENDING),
        ]

        for tid, status in tasks_data:
            task = DBTask(
                **TaskModelSchema(
                    task_id=tid,
                    payload={},
                    status=status,
                    has_logs=False,
                    idempotency_key=f"{tid}-key",
                ).model_dump()
            )
            db_session.add(task)
        await db_session.commit()

        # When
        pending_tasks = await task_repo.aget_tasks_by_status(TaskStatusEnum.PENDING)

        # Then
        assert len(pending_tasks) == 2
        assert all(t.status == TaskStatusEnum.PENDING.value for t in pending_tasks)

    async def test_acreate_tasks(self, db_session: AsyncSession) -> None:
        """Test batch creation of tasks."""
        # Given
        task_repo = TaskRepository(db_session)
        new_tasks = [
            TaskModelSchema(
                task_id=f"new_task_{i}",
                payload={"i": i},
                status=TaskStatusEnum.PENDING,
                has_logs=False,
                idempotency_key=f"new_task_{i}-key",
            )
            for i in range(3)
        ]

        # When
        await task_repo.acreate_tasks(new_tasks)

        # Then
        created_tasks = await task_repo.aget_tasks_by_ids([t.task_id for t in new_tasks])
        assert len(created_tasks) == 3

    async def test_aupdate_task_status(self, db_session: AsyncSession) -> None:
        """Test updating task status and timestamps."""
        # Given
        task_repo = TaskRepository(db_session)
        task_id = "status_update_test"

        task = DBTask(
            **TaskModelSchema(
                task_id=task_id,
                payload={},
                status=TaskStatusEnum.PENDING,
                has_logs=False,
                idempotency_key="status-update-key",
            ).model_dump()
        )
        db_session.add(task)
        await db_session.commit()

        # When: Update to IN_PROGRESS
        await task_repo.aupdate_task_status(task_id, TaskStatusEnum.IN_PROGRESS)

        # Then
        updated_task = await task_repo.aget_task_by_id(task_id)
        assert updated_task is not None
        assert updated_task.status == TaskStatusEnum.IN_PROGRESS.value
        assert updated_task.started_at is not None
        assert updated_task.completed_at is None

        # When: Update to COMPLETED
        await task_repo.aupdate_task_status(task_id, TaskStatusEnum.COMPLETED)

        # Then
        completed_task = await task_repo.aget_task_by_id(task_id)
        assert completed_task is not None
        assert completed_task.status == TaskStatusEnum.COMPLETED.value
        assert completed_task.completed_at is not None

    async def test_aupdate_log_info(self, db_session: AsyncSession) -> None:
        """Test updating log information."""
        # Given
        task_repo = TaskRepository(db_session)
        task_id = "log_test"

        task = DBTask(
            **TaskModelSchema(
                task_id=task_id,
                payload={},
                status=TaskStatusEnum.COMPLETED,
                has_logs=False,
                idempotency_key="log-test-key",
            ).model_dump()
        )
        db_session.add(task)
        await db_session.commit()

        # When
        await task_repo.aupdate_log_info(
            task_id, has_logs=True, log_s3_key="logs/test.log", log_s3_url="s3://bucket/logs/test.log"
        )

        # Then
        updated_task = await task_repo.aget_task_by_id(task_id)
        assert updated_task is not None
        assert updated_task.has_logs is True
        assert updated_task.log_s3_key == "logs/test.log"
        assert updated_task.log_s3_url == "s3://bucket/logs/test.log"

    async def test_aget_task_by_id_not_found(self, db_session: AsyncSession) -> None:
        """Test retrieving a task by its ID when the task does not exist."""
        # Given
        task_repo = TaskRepository(db_session)

        # When
        retrieved_task = await task_repo.aget_task_by_id("nonexistent")

        # Then
        assert retrieved_task is None

    async def test_aget_tasks_by_ids_not_found(self, db_session: AsyncSession) -> None:
        """Test retrieving multiple tasks by their IDs when some do not exist."""
        # Given
        task_repo = TaskRepository(db_session)

        # Create and add a test task
        test_task = DBTask(
            **TaskModelSchema(
                task_id="task1",
                payload={"key": "value1"},
                status=TaskStatusEnum.PENDING,
                has_logs=False,
                log_s3_key=None,
                log_s3_url=None,
                idempotency_key="task1-key",
            ).model_dump()
        )
        db_session.add(test_task)
        await db_session.commit()

        # When
        task_ids: list[str] = ["task1", "nonexistent"]
        retrieved_tasks: list[DBTask] = await task_repo.aget_tasks_by_ids(task_ids)

        # Then
        assert len(retrieved_tasks) == 1  # Only one task should be found
        assert retrieved_tasks[0].task_id == "task1"
