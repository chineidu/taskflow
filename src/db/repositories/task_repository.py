"""
Crud operations for the task repository.

(Using SQLAlchemy ORM v2.x)
"""

from datetime import datetime

from dateutil.parser import parse  # Very fast, handles ISO formats well
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from src import create_logger
from src.db.models import DBTask
from src.schemas.db.models import TaskModel
from src.schemas.types import TaskStatusEnum

logger = create_logger("crud")


class TaskRepository:
    """CRUD operations for the Task repository."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def aget_task_by_id(self, task_id: str) -> DBTask | None:
        """Get a task by its task ID."""
        stmt = select(DBTask).where(DBTask.task_id == task_id)
        return await self.db.scalar(stmt)

    async def aget_tasks_by_ids(self, task_ids: list[str]) -> list[DBTask]:
        """Get tasks by their task IDs."""
        stmt = select(DBTask).where(DBTask.task_id.in_(task_ids))
        result = await self.db.scalars(stmt)
        return list(result.all())

    async def aget_tasks_by_status(self, status: TaskStatusEnum) -> list[DBTask]:
        """Get tasks by their status."""
        stmt = select(DBTask).where(DBTask.status == status.value)
        result = await self.db.scalars(stmt)
        return list(result.all())

    async def aget_tasks_by_created_time(self, created_after: str, created_before: str) -> list[DBTask]:
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

    async def acreate_tasks(self, tasks: list[TaskModel]) -> None:
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

    async def aupdate_task(self, task: TaskModel) -> None:
        """Update a task in the database in a single round trip."""

        # Filter out fields that are None/excluded
        updated_data = {
            k: v
            for k, v in task.model_dump(exclude={"id", "created_at", "updated_at"}).items()
            if v is not None
        }
        if not updated_data:
            logger.info(f"No fields to update for task with task_id {task.task_id}. Skipping update.")
            return

        try:
            # Check existence first to keep compatibility across backends (RETURNING is not universal)
            exists_stmt = select(DBTask.task_id).where(DBTask.task_id == task.task_id)
            existing = await self.db.scalar(exists_stmt)
            if existing is None:
                raise ValueError(f"Task with task_id {task.task_id!r} does not exist. Cannot update.")

            stmt = update(DBTask).where(DBTask.task_id == task.task_id).values(**updated_data)
            await self.db.execute(stmt)
            await self.db.commit()
            logger.info(f"Successfully updated task with task_id {task.task_id!r}.")

        except Exception as e:
            logger.error(f"Error updating task with task_id {task.task_id!r}: {e}")
            await self.db.rollback()
            raise e

    async def abatch_update_tasks(self, tasks: list[TaskModel]) -> None:
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

    def convert_dbtask_to_schema(self, db_task: DBTask) -> TaskModel | None:
        """Convert a DBTask object to a TaskModel object."""
        try:
            return TaskModel(
                id=db_task.id,
                task_id=db_task.task_id,
                payload=db_task.payload,
                status=TaskStatusEnum(db_task.status),
                # Safer way to handle the datetime -> str conversion
                created_at=getattr(db_task.created_at, "isoformat", lambda **kwargs: None)(  # noqa: ARG005
                    timespec="seconds"
                ),
                updated_at=getattr(db_task.updated_at, "isoformat", lambda **kwargs: None)(  # noqa: ARG005
                    timespec="seconds"
                ),
                error_message=db_task.error_message,
            )
        except Exception as e:
            logger.error(f"Error converting DBTask to TaskModel: {e}")
            return None
