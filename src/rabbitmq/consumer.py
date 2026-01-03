import asyncio
import json
import random
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy.exc import DatabaseError

from src import add_file_handler, create_logger
from src.config import app_settings
from src.db.models import aget_db_session
from src.db.repositories.task_repository import TaskRepository
from src.rabbitmq.base import BaseRabbitMQ
from src.schemas.types import TaskStatusEnum
from src.services.storage import S3StorageService

if TYPE_CHECKING:
    from src.config.config import AppConfig

logger = create_logger("consumer")
RABBITMQ_URL: str = app_settings.rabbitmq_url

# Takes a [deserialized message dict] and returns [Any]
type CONSUMER_CALLBACK_FN = Callable[[dict[str, Any]], Any]


class RabbitMQConsumer(BaseRabbitMQ):
    """Production-ready RabbitMQ message consumer with callback support.

    Inherits connection management, retry logic, and queue handling from BaseRabbitMQ.
    """

    def __init__(self, config: "AppConfig", url: str = RABBITMQ_URL) -> None:
        """Initialize the consumer with configuration.

        Parameters
        ----------
        config : AppConfig
            Application configuration containing RabbitMQ settings.
        url : str
            RabbitMQ connection URL, by default RABBITMQ_URL.
        """
        super().__init__(config, url)
        self._shutdown_event = asyncio.Event()
        logger.info("[+] Consumer initialized")

    async def consume(
        self,
        queue_name: str,
        callback: CONSUMER_CALLBACK_FN,
        durable: bool = True,
    ) -> None:
        """Consume messages from queue and pass to callback

        Parameters
        ----------
        queue_name : str
            Name of the queue to consume from.
        callback : CONSUMER_CALLBACK_FN
            Async or sync callable that processes each message. Receives deserialized message dict.
        durable : bool, optional
            Whether the queue should be durable, by default True.
        """
        await self.aconnect()
        assert self.channel is not None, "Channel is not established."

        try:
            queue = await self.aensure_queue(queue_name=queue_name, durable=durable)
            logger.info(f"[+] Starting to consume from queue: {queue_name}")

            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    # Check if shutdown was requested
                    if self._shutdown_event.is_set():
                        logger.info("[+] Shutdown detected, breaking message loop")
                        break

                    task_id: str = "unknown"
                    try:
                        async with message.process(
                            requeue=False,
                            reject_on_redelivered=False,
                            ignore_processed=False,
                        ):
                            # Extract metadata for logging
                            task_id = str(message.headers.get("task_id", "unknown"))
                            correlation_id: str = message.correlation_id or "unknown"
                            timestamp = message.timestamp

                            logger.info(
                                f"[+] Received message | task_id={task_id} | "
                                f"correlation_id={correlation_id} | timestamp={timestamp}"
                            )
                            # Deserialize message
                            try:
                                message_body: str = message.body.decode()
                                message_dict = json.loads(message_body)

                            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                                logger.error(
                                    f"Failed to decode message with "
                                    f"correlation_id={message.correlation_id}: {e}"
                                )
                                continue

                            # ===== Initialize DB session and repository =====
                            try:
                                async with aget_db_session() as db:
                                    repo = TaskRepository(db)
                                    # Immediate State Change
                                    await repo.aupdate_task_status(task_id, status=TaskStatusEnum.IN_PROGRESS)
                                    logger.info(f"[*] Task {task_id} is now IN_PROGRESS")

                                    # ==== Retrievable logging info ====
                                    # All logs within this block will be captured and uploaded to S3
                                    # suffix=".log" helps S3 content-type detection
                                    with tempfile.NamedTemporaryFile(
                                        mode="w+", suffix=".log", delete=True
                                    ) as tmp_file:
                                        task_handler = add_file_handler(logger, tmp_file.name)
                                        temp_path = Path(tmp_file.name)
                                        s3_service = S3StorageService()

                                        # ===== Call the provided callback function =====
                                        try:
                                            if asyncio.iscoroutinefunction(callback):
                                                await callback(message_dict)
                                            else:
                                                callback(message_dict)

                                            logger.removeHandler(task_handler)
                                            # Stop logging and save the file.
                                            task_handler.close()
                                            # Upload logs to S3 if file has content
                                            if temp_path.stat().st_size > 0:
                                                await s3_service.aupload_file_to_s3(
                                                    filepath=temp_path,
                                                    task_id=task_id,
                                                    correlation_id=correlation_id,
                                                    environment=app_settings.ENVIRONMENT,
                                                )
                                                # Update logs info in DB
                                                s3_key = s3_service.get_object_name(task_id)
                                                s3_url = s3_service.get_s3_object_url(task_id)
                                                await repo.aupdate_log_info(
                                                    task_id,
                                                    has_logs=True,
                                                    log_s3_key=s3_key,
                                                    log_s3_url=s3_url,
                                                )
                                                logger.info(
                                                    f"[+] Task {task_id} uploaded logs to S3 and "
                                                    "COMPLETED successfully"
                                                )

                                            else:
                                                await repo.aupdate_log_info(
                                                    task_id,
                                                    has_logs=False,
                                                )
                                                logger.warning(f"[x] No logs to upload for task {task_id}")

                                            # Final State: SUCCESS
                                            await repo.aupdate_task_status(
                                                task_id, status=TaskStatusEnum.COMPLETED
                                            )

                                        except Exception as callback_error:
                                            # Final State: FAILURE
                                            # Cleanup logging handler and upload the logs collected so far
                                            logger.removeHandler(task_handler)
                                            task_handler.close()

                                            # Log even if callback failed
                                            if temp_path.exists() and temp_path.stat().st_size > 0:
                                                await s3_service.aupload_file_to_s3(
                                                    filepath=temp_path,
                                                    task_id=task_id,
                                                    correlation_id=correlation_id,
                                                    environment=app_settings.ENVIRONMENT,
                                                )
                                                # Update logs info in DB
                                                s3_key = s3_service.get_object_name(task_id)
                                                s3_url = s3_service.get_s3_object_url(task_id)
                                                await repo.aupdate_log_info(
                                                    task_id,
                                                    has_logs=True,
                                                    log_s3_key=s3_key,
                                                    log_s3_url=s3_url,
                                                )
                                                logger.info(
                                                    f"[+] Task {task_id} uploaded logs to S3 after FAILURE"
                                                )
                                            else:
                                                await repo.aupdate_log_info(
                                                    task_id,
                                                    has_logs=False,
                                                )
                                            await repo.aupdate_task_status(
                                                task_id,
                                                status=TaskStatusEnum.FAILED,
                                                error=str(callback_error),
                                            )
                                            logger.error(f"[x] Task {task_id} FAILED: {callback_error}")

                            except DatabaseError as db_err:
                                logger.error(f"[x] Database error for task_id={task_id}: {db_err}")
                                # Requeue the message for later processing
                                raise db_err

                    except asyncio.CancelledError:
                        logger.info("[+] Message processing cancelled")
                        raise

                    except Exception as e:
                        logger.error(f"[x] Error processing task_id={task_id}: {e}")

            logger.info("[+] Exiting consume loop")

        except asyncio.CancelledError:
            logger.info("[+] Consumer task cancelled")
            raise
        except Exception as e:
            logger.error(f"Unexpected error consuming from queue '{queue_name}': {e}")


# ================ Example Usage ================
async def process_data_chunk(chunk_id: int, data: dict[str, Any]) -> None:  # noqa: ARG001
    """Simulates a nested function to test if child logs are captured."""
    logger.debug(f"  [Sub-Task] Starting sub-chunk {chunk_id} processing...")
    await asyncio.sleep(0.6)

    if random.random() < 0.1:  # 10% chance to log a warning
        logger.warning(f"  [Sub-Task] Chunk {chunk_id} detected high memory pressure.")

    logger.debug(f"  [Sub-Task] Chunk {chunk_id} successfully transformed.")


async def example_consumer_callback(message: dict[str, Any]) -> None:
    """
    Complex callback to test multi-level logging capture and S3 upload.
    """
    start_time = time.perf_counter()
    task_data = message

    logger.info("🚀 Starting high-complexity execution...")
    logger.info(f"Target Payload: {json.dumps(task_data, indent=2)}")

    try:
        # Phase 1: Validation
        logger.info("Phase 1: Validating input schema...")
        if not task_data:
            logger.error("❌ Validation Failed: Empty payload received.")
            raise ValueError("Empty Payload")
        await asyncio.sleep(0.3)
        logger.info("✅ Validation complete.")

        # Phase 2: Transformation (Nested Logging)
        logger.info("Phase 2: Processing data chunks...")
        for i in range(1, 4):
            await process_data_chunk(i, task_data)
        logger.info("✅ All 3 chunks processed.")

        # Phase 3: External 'Service' simulation
        logger.info("Phase 3: Communicating with external AI service...")
        await asyncio.sleep(0.5)
        # Randomly simulate an 'insight' for the log
        confidence = random.uniform(0.85, 0.99)
        logger.info(f"🤖 AI Inference complete. Confidence Score: {confidence:.2%}")

        # Summary
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.info(f"🏁 Task execution finished successfully in {duration:.2f} seconds.")

    except Exception as e:
        logger.exception(f"💥 Fatal error during callback execution: {str(e)}")
        raise  # Re-raise to let the consumer handle the FAILED status update


async def run_worker() -> None:
    """Example usage of the consumer."""
    from src.config import app_config

    consumer = RabbitMQConsumer(app_config)

    # Setup signal handlers for graceful shutdown
    def signal_handler(sig: int) -> None:
        """Handle shutdown signals."""
        logger.info(f"[+] Received signal {sig}, initiating graceful shutdown...")
        consumer._shutdown_event.set()

    # Register signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    try:
        # Consume with async callback using context manager
        async with consumer.aconnection_context():
            # Create consume task
            consume_task = asyncio.create_task(
                consumer.consume(
                    queue_name=app_config.rabbitmq_config.queue_names.task_queue,
                    callback=example_consumer_callback,
                    durable=True,
                )
            )

            # Wait for shutdown event
            await consumer._shutdown_event.wait()

            # Cancel consume task
            logger.info("[+] Cancelling consume task...")
            consume_task.cancel()
            try:
                await consume_task
            except asyncio.CancelledError:
                logger.info("[+] Consume task cancelled successfully")

        logger.info("[+] Consumer shutdown complete")

    except asyncio.CancelledError:
        logger.info("[+] Consumer cancelled, cleaning up...")
    except Exception as e:
        logger.error(f"[x] Error in main: {e}")
        raise


async def main() -> None:
    """Entry point to run the consumer with S3 bucket check."""
    s3_service = S3StorageService()
    if not await s3_service.acheck_bucket_exists():
        logger.error("[x] S3 bucket not accessible")
        sys.exit(1)
    logger.info("[+] S3 bucket is accessible, starting consumer...")

    await run_worker()


if __name__ == "__main__":
    try:
        asyncio.run(main())
        logger.info("[+] Exiting gracefully")

    except KeyboardInterrupt:
        logger.info("[+] Received KeyboardInterrupt, exiting...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"[x] Fatal error running consumer: {e}")
        sys.exit(1)
