"""
Crud operations for the task repository.

(Using SQLAlchemy ORM v2.x)
"""

from datetime import datetime, timedelta
from typing import Any

from dateutil.parser import parse  # Very fast, handles ISO formats well
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from src import create_logger
from src.db.models import DBTask
from src.schemas.db.models import DashboardTaskStats, TaskModelSchema
from src.schemas.types import TaskStatusEnum

logger = create_logger("repo.task_repository")


class TaskRepository:
    """CRUD operations for the Task repository."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def aget_task_by_id(self, task_id: str) -> DBTask | None:
        """Get a task by its task ID."""
        try:
            stmt = select(DBTask).where(DBTask.task_id == task_id)
            return await self.db.scalar(stmt)
        except Exception as e:
            logger.error(f"Error fetching task by id '{task_id}': {e}")
            return None

    async def aget_tasks_by_idempotency_key(self, idempotency_key: str) -> list[DBTask]:
        """Get tasks by their idempotency key."""
        try:
            stmt = select(DBTask).where(DBTask.idempotency_key.like(f"{idempotency_key}%"))
            result = await self.db.scalars(stmt)
            return list(result.all())
        except Exception as e:
            logger.error(f"Error fetching task by idempotency key '{idempotency_key}': {e}")
            return []

    async def aget_tasks_by_ids(self, task_ids: list[str]) -> list[DBTask]:
        """Get tasks by their task IDs."""
        try:
            stmt = select(DBTask).where(DBTask.task_id.in_(task_ids))
            result = await self.db.scalars(stmt)
            return list(result.all())
        except Exception as e:
            logger.error(f"Error fetching tasks by ids {task_ids}: {e}")
            return []

    async def aget_tasks_by_status(self, status: TaskStatusEnum) -> list[DBTask]:
        """Get tasks by their status."""
        try:
            stmt = select(DBTask).where(DBTask.status == status.value)
            result = await self.db.scalars(stmt)
            return list(result.all())
        except Exception as e:
            logger.error(f"Error fetching tasks by status {status}: {e}")
            return []

    async def aget_average_processing_time(self) -> float:
        """Calculate the average processing time for completed tasks.

        Parameters
        ----------
        limit : int
            The maximum number of recent completed tasks to consider (default: 500).

        Returns
        -------
        float
            The average processing time in seconds. Returns 0.0 if no completed tasks are found.
        """
        try:
            # Using `extract("epoch", ...)` extracts the epoch time difference between 
            # completed_at and started_at.i.e. final result is in seconds
            stmt = select(
                func.round(func.avg(func.extract("epoch", DBTask.completed_at - DBTask.started_at)), 2).label(
                    "avg_duration_seconds"
                )
            ).where(
                DBTask.status == TaskStatusEnum.COMPLETED.value,
                DBTask.completed_at.isnot(None),
                DBTask.started_at.isnot(None),
                DBTask.completed_at >= DBTask.started_at,
            )
            avg_time = await self.db.scalar(stmt)
            return avg_time if avg_time else 0.0

        except Exception as e:
            logger.error(f"Error calculating average processing time: {e}")
            return 0.0

    async def aget_dashboard_metrics(self) -> DashboardTaskStats:
        """Get tasks created within a specific time range. Uses database-level comparison.

        Parameters
        ----------
        None

        Returns
        -------
        DashboardTaskStats
            Dashboard metrics including status counts and DLQ counts.
        """
        # NB: Use `.execute()` for multi-column queries
        try:
            status_stmt = select(DBTask.status, func.count(DBTask.id)).group_by(DBTask.status)
            status_result = await self.db.execute(status_stmt)
            # Returns `status: count`. e.g. {'pending': 10, 'completed': 65, 'inprogress': 20, 'failed': 5}
            status_mapping = dict(status_result.all())  # type: ignore

            dlq_stmt = (
                select(func.count(DBTask.id))
                # i.e. where tasks are in the DLQ
                .where(DBTask.in_dlq)
                .order_by(func.count(DBTask.id).desc())
            )
            dlq_result = await self.db.scalar(dlq_stmt)

            return DashboardTaskStats(
                total_tasks=sum(status_mapping.values()),
                pending=status_mapping.get(TaskStatusEnum.PENDING.value, 0),
                in_progress=status_mapping.get(TaskStatusEnum.IN_PROGRESS.value, 0),
                completed=status_mapping.get(TaskStatusEnum.COMPLETED.value, 0),
                failed=status_mapping.get(TaskStatusEnum.FAILED.value, 0),
                dlq_counts=dlq_result or 0,
            )

        except Exception as e:
            logger.error(f"Error getting dashboard metrics: {e}")
            return DashboardTaskStats(
                total_tasks=0, pending=0, in_progress=0, completed=0, failed=0, dlq_counts=0
            )

    async def aget_tasks_paginated(
        self,
        status: TaskStatusEnum | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[DBTask], int]:
        """Get tasks with pagination and optional status filter.

        Parameters
        ----------
        status : TaskStatusEnum | None
            Filter by task status. If None, returns all tasks.
        limit : int
            Maximum number of tasks to return (default: 10).
        offset : int
            Number of tasks to skip (default: 0).

        Returns
        -------
        tuple[list[DBTask], int]
            A tuple of (tasks, total_count) where tasks is the paginated list
            and total_count is the total number of tasks matching the filter.
        """
        try:
            # 1. Prepare base filtering
            # Using a list of filters makes it easy to add more (e.g., date ranges) later
            filters = []
            if status:
                filters.append(DBTask.status == status.value)

            # 2. Get TOTAL COUNT (Let Postgres do the work)
            # This returns a single aggregate (value) instead of 100,000 IDs
            count_stmt = select(func.count()).select_from(DBTask).where(*filters)
            total = await self.db.scalar(count_stmt) or 0

            # 3. Get PAGINATED RESULTS
            # Ensure the order matches your index for maximum speed
            stmt = (
                select(DBTask).where(*filters).order_by(DBTask.created_at.desc()).limit(limit).offset(offset)
            )

            result = await self.db.scalars(stmt)
            tasks = list(result.all())

            return (tasks, total)
        except Exception as e:
            logger.error(f"Error fetching paginated tasks: {e}")
            return ([], 0)

    async def aget_tasks_by_creation_time(self, created_after: str, created_before: str) -> list[DBTask]:
        """Get tasks created within a specific time range. Uses database-level comparison.

        Parameters
        ----------
        created_after : str
            The start timestamp (inclusive). e.g. "2023-01-01T00:00:00"
        created_before : str
            The end timestamp (inclusive). e.g. "2023-01-31T23:59:59"

        Returns
        -------
        list[DBTask]
            List of tasks created within the specified time range.
        """
        # Internal check: ensures the strings are at least valid dates
        # before hitting the DB
        try:
            start: datetime = parse(created_after)
            end: datetime = parse(created_before)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid date format passed to query: {e}")
            raise ValueError("Timestamps must be valid ISO 8601 strings.") from e

        stmt = select(DBTask).where(
            DBTask.created_at >= start,
            DBTask.created_at <= end,
        )
        result = await self.db.scalars(stmt)
        return list(result.all())

    async def aget_tasks_by_updated_time(self, updated_after: str, updated_before: str) -> list[DBTask]:
        """Get tasks updated within a specific time range. Uses database-level comparison.

        Parameters
        ----------
        updated_after : str
            The start timestamp (inclusive). e.g. "2023-01-01T00:00:00"
        updated_before : str
            The end timestamp (inclusive). e.g. "2023-01-31T23:59:59"

        Returns
        -------
        list[DBTask]
            List of tasks updated within the specified time range.
        """
        # Internal check: ensures the strings are at least valid dates
        # before hitting the DB
        try:
            start: datetime = parse(updated_after)
            end: datetime = parse(updated_before)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid date format passed to query: {e}")
            raise ValueError("Timestamps must be valid ISO 8601 strings.") from e

        stmt = select(DBTask).where(
            DBTask.updated_at >= start,
            DBTask.updated_at <= end,
        )
        result = await self.db.scalars(stmt)
        return list(result.all())

    async def acreate_tasks(self, tasks: list[TaskModelSchema]) -> None:
        """Batch create tasks in the database."""
        try:
            db_tasks = [
                DBTask(**task.model_dump(exclude={"id", "created_at", "updated_at"})) for task in tasks
            ]
        except Exception as e:
            logger.error(f"Error preparing tasks for creation: {e}")
            raise e

        try:
            self.db.add_all(db_tasks)
            await self.db.commit()
            logger.info(f"Successfully created {len(db_tasks)!r} tasks in the database.")

        except IntegrityError as e:
            logger.error(f"Integrity error creating tasks: {e}")
            await self.db.rollback()
            raise e

        except Exception as e:
            logger.error(f"Error creating tasks: {e}")
            await self.db.rollback()
            raise e

    async def aupdate_task(self, task: TaskModelSchema) -> None:
        """Update a task in the database in a single round trip."""

        # Filter out fields that are None/excluded
        update_data = {
            k: v
            for k, v in task.model_dump(exclude={"id", "created_at", "updated_at"}).items()
            if v is not None
        }
        if not update_data:
            logger.info(f"No fields to update for task with task_id {task.task_id}. Skipping update.")
            return

        try:
            # Check existence first to keep compatibility across backends (RETURNING is not universal)
            exists_stmt = select(DBTask.task_id).where(DBTask.task_id == task.task_id)
            existing = await self.db.scalar(exists_stmt)
            if existing is None:
                raise ValueError(f"Task with task_id {task.task_id!r} does not exist. Cannot update.")

            stmt = update(DBTask).where(DBTask.task_id == task.task_id).values(**update_data)
            await self.db.execute(stmt)
            await self.db.commit()
            logger.info(f"Successfully updated task with task_id {task.task_id!r}.")

        except Exception as e:
            logger.error(f"Error updating task with task_id {task.task_id!r}: {e}")
            await self.db.rollback()
            raise e

    async def abatch_update_tasks(self, tasks: list[TaskModelSchema]) -> None:
        """Batch update tasks in the database."""
        task_ids = [task.task_id for task in tasks]
        existing_tasks = await self.aget_tasks_by_ids(task_ids)
        existing_tasks_dict = {task.task_id: task for task in existing_tasks}

        for task in tasks:
            existing_task = existing_tasks_dict.get(task.task_id)
            if not existing_task:
                logger.warning(f"Task with task_id {task.task_id!r} does not exist. Skipping update.")
                continue

            # Filter out fields that are None/excluded
            updated_values = {
                k: v
                for k, v in task.model_dump(exclude={"id", "created_at", "updated_at"}).items()
                if v is not None
            }
            # Update only the fields that are provided
            for field, value in updated_values.items():
                setattr(existing_task, field, value)

        try:
            self.db.add_all(existing_tasks)
            await self.db.commit()
            logger.info(
                f"Successfully completed batch update of {len(existing_tasks)} tasks in the database."
            )

        except Exception as e:
            logger.error(f"Error batch updating tasks: {e}")
            await self.db.rollback()
            raise e

    async def aupdate_task_status(
        self, task_id: str, status: TaskStatusEnum, error: str | None = None
    ) -> None:
        """Mark a task as IN_PROGRESS, COMPLETED, FAILED, etc.

        Parameters
        ----------
        task_id : str
            The unique task identifier.
        status : TaskStatusEnum
            The new status to set for the task.
        error : str | None
            Optional error message if the task failed.

        Raises
        ------
        Exception
            If the update fails.
        """
        try:
            update_values: dict[str, Any] = {"status": status.value}

            # Automatically set timestamps based on status
            now = func.now()
            if status == TaskStatusEnum.IN_PROGRESS:
                update_values["started_at"] = now
            elif status in (TaskStatusEnum.COMPLETED, TaskStatusEnum.FAILED):
                update_values["completed_at"] = now

            if error:
                # If marking as FAILED, set the error message
                update_values["error_message"] = error
            elif status == TaskStatusEnum.COMPLETED:
                # Clear error message on successful completion
                update_values["error_message"] = None

            stmt = update(DBTask).where(DBTask.task_id == task_id).values(**update_values)
            await self.db.execute(stmt)
            await self.db.commit()
            logger.info(f"Marked task_id='{task_id}' as {status.name}.")

        except Exception as e:
            logger.error(f"Error marking task_id='{task_id}' as {status.name}: {e}")
            await self.db.rollback()
            raise e

    async def aupdate_log_info(
        self, task_id: str, has_logs: bool, log_s3_key: str | None = None, log_s3_url: str | None = None
    ) -> None:
        """Atomically update log metadata for a task.

        Parameters
        ----------
        task_id : str
            The unique task identifier.
        has_logs : bool
            Whether the task has execution logs.
        log_s3_key : str | None
            S3 object key where the log file is stored.
        log_s3_url : str | None
            Full URL to access the log file from S3.

        Raises
        ------
        Exception
            If the update fails.
        """
        try:
            stmt = (
                update(DBTask)
                .where(DBTask.task_id == task_id)
                .values(
                    has_logs=has_logs,
                    log_s3_key=log_s3_key,
                    log_s3_url=log_s3_url,
                )
            )
            await self.db.execute(stmt)
            await self.db.commit()
            logger.info(f"Updated log info for task_id='{task_id}'. has_logs={has_logs}")

        except Exception as e:
            logger.error(f"Error updating log info for task_id='{task_id}': {e}")
            await self.db.rollback()
            raise e

    async def aupdate_dlq_status(self, task_id: str, in_dlq: bool) -> None:
        """Update the DLQ status of a task.

        Parameters
        ----------
        task_id : str
            The unique task identifier.
        in_dlq : bool
            Whether the task is currently in the Dead Letter Queue.

        Raises
        ------
        Exception
            If the update fails.
        """
        try:
            stmt = (
                update(DBTask)
                .where(DBTask.task_id == task_id)
                .values(
                    in_dlq=in_dlq,
                )
            )
            await self.db.execute(stmt)
            await self.db.commit()
            logger.info(f"Updated DLQ status for task_id='{task_id}'. in_dlq={in_dlq}")

        except Exception as e:
            logger.error(f"Error updating DLQ status for task_id='{task_id}': {e}")
            await self.db.rollback()
            raise e

    async def acleanup_expired_tasks(self, timeout_seconds: int = 360) -> int:
        """Cleanup tasks that have been in IN_PROGRESS state for too long. Add a small buffer
        and markes them as FAILED.

        Parameters
        ----------
        timeout_seconds : int
            The threshold in seconds to consider a task as expired (default: 360 seconds).

        Returns
        -------
        int
            The number of tasks that were marked as FAILED due to expiration.

        Raises
        ------
        Exception
            If the cleanup operation fails.
        """
        try:
            threshold = datetime.now() - timedelta(seconds=timeout_seconds)
            filter = (
                DBTask.status == TaskStatusEnum.IN_PROGRESS.value,
                # If the task has been IN_PROGRESS for longer than the threshold
                DBTask.updated_at < threshold,
            )
            stmt = (
                update(DBTask)
                .where(*filter)
                .values(
                    status=TaskStatusEnum.FAILED.value,
                    error_message="Task expired due to timeout.",
                    completed_at=func.now(),
                )
            )
            await self.db.execute(stmt)
            count_stmt = select(func.count()).select_from(DBTask).where(*filter)
            count = await self.db.scalar(count_stmt) or 0
            await self.db.commit()
            logger.info(f"Cleaned up {count} expired tasks from IN_PROGRESS state.")
            return count

        except Exception as e:
            logger.error(f"Error cleaning up expired tasks: {e}")
            await self.db.rollback()
            raise e

    def convert_dbtask_to_schema(self, db_task: DBTask) -> TaskModelSchema | None:
        """Convert a DBTask ORM object directly to a Pydantic response schema."""
        try:
            return TaskModelSchema.model_validate(db_task)
        except Exception as e:
            logger.error(f"Error converting DBTask to TaskModelSchema: {e}")
            return None
