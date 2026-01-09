from src import create_logger
from src.db.models import aget_db_session
from src.db.repositories.task_repository import TaskRepository

logger = create_logger("scripts.tasks_cleanup")
TIMEOUT_SECONDS = 3600  # Define the timeout duration for task expiration
SLEEP_SECONDS = 60  # Sleep duration to allow logger to flush

async def arun_cleanup() -> None:
    """Asynchronous function to clean up old tasks from the database."""
    async with aget_db_session() as session:
        tasks_repo = TaskRepository(session)
        cleaned_rows = await tasks_repo.acleanup_expired_tasks(timeout_seconds=TIMEOUT_SECONDS)
    if cleaned_rows:
        logger.info(f"✅ Cleaned up {cleaned_rows} expired tasks.")
    else:
        logger.info("🚫 No expired tasks to clean up.")


async def amain() -> None:
    """Main asynchronous entry point for the script."""
    while True:
        await arun_cleanup()
        await asyncio.sleep(SLEEP_SECONDS)  # Allow logger to flush

if __name__ == "__main__":
    import asyncio

    asyncio.run(amain())
