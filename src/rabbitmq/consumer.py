import asyncio
import json
import random
import signal
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from sqlalchemy.exc import DatabaseError

from src import add_file_handler, create_logger
from src.config import app_config, app_settings
from src.db.models import aget_db_session
from src.db.repositories.task_repository import TaskRepository
from src.rabbitmq.base import BaseRabbitMQ
from src.rabbitmq.producer import RabbitMQProducer
from src.rabbitmq.utilities import map_priority_enum_to_int, queue_result_on_completion
from src.schemas.rabbitmq.base import QueueArguments
from src.schemas.types import ErrorCodeEnum, PriorityEnum, TaskStatusEnum
from src.services.storage import S3StorageService
from src.utilities import CircuitBreaker

if TYPE_CHECKING:
    from src.config.config import AppConfig

logger = create_logger("rabbitmq.consumer", structured=True)
RABBITMQ_URL: str = app_settings.rabbitmq_url
MAX_RETRIES: int = app_config.rabbitmq_config.max_retries
DELAY_BETWEEN_RETRIES: int = app_config.rabbitmq_config.retry_backoff_delay
SYSTEM_FAILURE_THRESHOLD: int = app_config.rabbitmq_config.circuit_breaker_config.failure_threshold
SYSTEM_RECOVERY_TIMEOUT: int = app_config.rabbitmq_config.circuit_breaker_config.recovery_timeout

# Takes a [deserialized message dict] and returns [Any]
type CONSUMER_CALLBACK_FN = Callable[[dict[str, Any]], Awaitable[None] | Any]


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
        self.breaker = CircuitBreaker(
            failure_threshold=SYSTEM_FAILURE_THRESHOLD, recovery_timeout=SYSTEM_RECOVERY_TIMEOUT
        )
        logger.info("[+] Consumer initialized")

    async def consume(
        self,
        queue_name: str,
        callback: CONSUMER_CALLBACK_FN,
        durable: bool = True,
    ) -> None:
        """Consume messages from queue and pass to callback with robust error handling and retry logic.

        Parameters
        ----------
        queue_name : str
            Name of the queue to consume from.
        callback : CONSUMER_CALLBACK_FN
            Async or sync callable that processes each message. Receives deserialized message dict.
        durable : bool, optional
            Whether the queue should be durable, by default True.
        """
        # --------------- Infrastructure Setup ---------------
        await self.aconnect()
        producer = RabbitMQProducer(self.config)  # Used for re-publishing retries
        await producer.aconnect()  # Ensure producer is connected before use

        s3_service = S3StorageService()
        priority = app_config.rabbitmq_config.queue_priority
        priority_int = map_priority_enum_to_int(priority)

        assert self.channel is not None, "Channel is not established."

        await self.aensure_dlq(
            dlq_name=self.config.rabbitmq_config.dlq_config.dlq_name,
            dlx_name=self.config.rabbitmq_config.dlq_config.dlx_name,
        )

        queue = await self.aensure_queue(
            queue_name=queue_name,
            # Attach dead-letter exchange for failed messages
            arguments=QueueArguments(
                x_dead_letter_exchange=self.config.rabbitmq_config.dlq_config.dlx_name,
                x_max_priority=priority_int,
            ),
            durable=durable,
        )
        # Wait queue with N ttl for retries
        delay_queue_name: str = f"{queue_name}_delay"
        await self.aensure_delay_queue(
            delay_queue_name=delay_queue_name,
            target_queue_name=queue_name,
            ttl_ms=self.config.rabbitmq_config.dlq_config.ttl,
        )

        logger.info(f"[+] Starting to consume from queue: '{queue_name}'")

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                # Check if shutdown was requested
                if self._shutdown_event.is_set():
                    logger.info("[+] Shutdown detected, breaking message loop")
                    break

                # Extract metadata for logging
                task_id: str = "unknown"
                headers: dict[str, Any] = message.headers or {}
                task_id = str(headers.get("task_id", "unknown"))
                correlation_id: str = message.correlation_id or "unknown"
                timestamp: datetime | None = message.timestamp
                retry_count: int = headers.get("x-retry-count", 0)
                idempotency_key: str = headers.get("Idempotency-Key", "none")

                # --------------- Circuit Breaker Check ---------------
                # If circuit breaker is OPEN, route message to delay queue without processing
                if not self.breaker.can_execute():
                    logger.warning(
                        f"Circuit Breaker is ({self.breaker.state.upper()}). "
                        f"Skipping message processing and moving message to '{delay_queue_name}'."
                    )
                    retry_headers = headers.copy()
                    # Update circuit breaker state in headers for downstream consumers
                    # Don't increment retry count yet
                    retry_headers["x-circuit-breaker"] = self.breaker.state
                    rmq_success, _ = await producer.apublish(
                        message=json.loads(message.body.decode()),
                        queue_name=delay_queue_name,
                        priority=PriorityEnum.MEDIUM,
                        headers=retry_headers,
                        request_id=correlation_id,
                        task_id=task_id,
                        durable=durable,
                    )
                    if rmq_success:
                        logger.info(
                            f"[+] Message task_id={task_id} routed to '{delay_queue_name}' due "
                            f"to {self.breaker.state.upper()} circuit breaker"
                        )
                        # Acknowledge current message to remove from queue
                        await message.ack()
                        continue
                    logger.error(
                        f"[-] {ErrorCodeEnum.RABBITMQ_ERROR}: Failed to route message task_id={task_id} to "
                        f"'{delay_queue_name}' during {self.breaker.state.upper()} state"
                    )
                    # Do not ack, let it be retried later
                    continue

                logger.info(
                    f"[+] Received message | task_id={task_id} | "
                    f"correlation_id={correlation_id} | timestamp={timestamp}"
                )
                # --------------- Core Processing ---------------
                try:
                    logger.debug(
                        f"[*] Processing task_id={task_id} | Attempt {retry_count}/{MAX_RETRIES} "
                        f"failure_count={self.breaker.failure_count}/{self.breaker.failure_threshold}. \n"
                    )
                    # Deserialize message
                    message_body: str = message.body.decode()
                    message_dict = json.loads(message_body)

                    # --------------- Initialize DB session and repository ---------------
                    try:
                        async with aget_db_session() as db:
                            task_repo = TaskRepository(db)
                            # Check if idempotency key exists in the system
                            tasks = await task_repo.aget_tasks_by_idempotency_key(idempotency_key)
                            if tasks and all(task.status == TaskStatusEnum.COMPLETED for task in tasks):
                                logger.info(
                                    f"[x] Duplicate message detected with idempotency_key={idempotency_key}. "
                                    f"Acknowledging and skipping processing for task_id={tasks[0].task_id}"
                                )
                                # Acknowledge message to remove from queue
                                await message.ack()
                                continue

                            # Check for existing task status. If already COMPLETED, ACK and skip processing
                            # or if inprogress possibly due to worker crash, we can choose to reprocess
                            task = await task_repo.aget_task_by_id(task_id)
                            if task and task.status == TaskStatusEnum.COMPLETED:
                                logger.info(
                                    f"[x] Task {task_id} already COMPLETED. "
                                    "Acknowledging and skipping processing."
                                )
                                await message.ack()
                                continue

                            if task and task.status == TaskStatusEnum.IN_PROGRESS:
                                logger.info(f"[x] Task {task_id} was IN_PROGRESS. Resuming the task...")

                            # Immediate State Change
                            await task_repo.aupdate_task_status(task_id, status=TaskStatusEnum.IN_PROGRESS)

                            # ==== Retrievable logging info ====
                            # All logs within this block will be captured and uploaded to S3
                            # suffix=".log" helps S3 content-type detection
                            with tempfile.NamedTemporaryFile(
                                mode="w+", suffix=".log", delete=True
                            ) as tmp_file:
                                task_handler = add_file_handler(logger, tmp_file.name, structured=True)
                                temp_path = Path(tmp_file.name)

                                # ----------- Call the provided callback function -----------
                                # Business logic execution happens here
                                try:
                                    # ----------- Task timeout enforcement -----------
                                    if asyncio.iscoroutinefunction(callback):
                                        await asyncio.wait_for(
                                            callback(message_dict),
                                            timeout=self.config.rabbitmq_config.tasks_timeout,
                                        )
                                    else:
                                        # Convert sync function to async and run with timeout
                                        _async_version = asyncio.to_thread(callback, message_dict)
                                        await asyncio.wait_for(
                                            _async_version, timeout=self.config.rabbitmq_config.tasks_timeout
                                        )

                                    logger.removeHandler(task_handler)
                                    # Stop logging and save the file.
                                    task_handler.close()
                                    # Upload logs to S3 if file has content
                                    if temp_path.stat().st_size > 0:
                                        s3_success = await s3_service.aupload_file_to_s3(
                                            filepath=temp_path,
                                            task_id=task_id,
                                            correlation_id=correlation_id,
                                            environment=app_settings.ENV,
                                        )
                                        if s3_success:
                                            # Update logs info in DB
                                            s3_key = s3_service.get_object_name(task_id)
                                            s3_url = s3_service.get_s3_object_url(task_id)
                                            await task_repo.aupdate_log_info(
                                                task_id,
                                                has_logs=True,
                                                log_s3_key=s3_key,
                                                log_s3_url=s3_url,
                                            )
                                        else:
                                            logger.error(
                                                f"{ErrorCodeEnum.BLOB_STORAGE_ERROR}: [x] Failed to upload "
                                                f"logs to S3 for task {task_id}"
                                            )

                                    else:
                                        await task_repo.aupdate_log_info(
                                            task_id,
                                            has_logs=False,
                                        )

                                        logger.warning(f"[x] No logs to upload for task {task_id}")

                                    # Final State: SUCCESS
                                    await task_repo.aupdate_task_status(
                                        task_id, status=TaskStatusEnum.COMPLETED
                                    )
                                    await task_repo.aupdate_dlq_status(task_id, in_dlq=False)
                                    # Acknowledge message and remove from queue
                                    await message.ack()
                                    self.breaker.record_success()
                                    logger.info(f"[+] Task {task_id} COMPLETED successfully")

                                except (ValueError, KeyError, TypeError) as biz_logic_err:
                                    # Business logic error inside callback. These errors are not retried
                                    # because they can't be fixed until manual fix is done
                                    logger.error(
                                        f"[x] {ErrorCodeEnum.BUSINESS_LOGIC_ERROR} in task "
                                        f"{task_id}: {biz_logic_err}"
                                    )
                                    # Cleanup logging handler and upload the logs collected so far
                                    logger.removeHandler(task_handler)
                                    task_handler.close()

                                    if temp_path.exists() and temp_path.stat().st_size > 0:
                                        s3_success = await s3_service.aupload_file_to_s3(
                                            filepath=temp_path,
                                            task_id=task_id,
                                            correlation_id=correlation_id,
                                            environment=app_settings.ENV,
                                        )
                                        if s3_success:
                                            # Update logs info in DB
                                            s3_key = s3_service.get_object_name(task_id)
                                            s3_url = s3_service.get_s3_object_url(task_id)
                                            await task_repo.aupdate_log_info(
                                                task_id,
                                                has_logs=True,
                                                log_s3_key=s3_key,
                                                log_s3_url=s3_url,
                                            )
                                            logger.info(
                                                f"[+] Task {task_id} uploaded logs to S3 after BUSINESS ERROR"
                                            )
                                        else:
                                            logger.error(
                                                f"[x] {ErrorCodeEnum.BUSINESS_LOGIC_ERROR}: Failed to "
                                                "upload logs to S3 for task {task_id} after BUSINESS ERROR"
                                            )
                                    else:
                                        await task_repo.aupdate_log_info(
                                            task_id,
                                            has_logs=False,
                                        )

                                    # Mark task as FAILED, do not retry
                                    # (no amount of retries will fix business logic errors)
                                    await task_repo.aupdate_task_status(
                                        task_id,
                                        status=TaskStatusEnum.FAILED,
                                        error=f"{ErrorCodeEnum.BUSINESS_LOGIC_ERROR}: {str(biz_logic_err)}",
                                    )
                                    # Acknowledge message to remove from queue
                                    await message.ack()
                                    logger.info(
                                        f"[+] Task {task_id} marked as FAILED due to business logic error"
                                    )

                                except asyncio.TimeoutError as timeout_err:
                                    logger.error(
                                        f"[x] {ErrorCodeEnum.TIMEOUT_ERROR}: Task {task_id} timed out"
                                        f"after {self.config.rabbitmq_config.tasks_timeout} seconds:"
                                        f" {timeout_err} "
                                    )
                                    # Propagate to retry logic. Retrying may help if timeout was due
                                    # to transient issues
                                    raise Exception(
                                        f"Task {task_id} timed out after "
                                        f"{self.config.rabbitmq_config.tasks_timeout} seconds"
                                    ) from timeout_err

                                except Exception as callback_error:
                                    # Other unexpected errors from callback
                                    # Cleanup logging handler and upload the logs
                                    # collected so far
                                    logger.removeHandler(task_handler)
                                    task_handler.close()

                                    # Log even if callback failed
                                    if temp_path.exists() and temp_path.stat().st_size > 0:
                                        s3_success = await s3_service.aupload_file_to_s3(
                                            filepath=temp_path,
                                            task_id=task_id,
                                            correlation_id=correlation_id,
                                            environment=app_settings.ENV,
                                        )
                                        if s3_success:
                                            # Update logs info in DB
                                            s3_key = s3_service.get_object_name(task_id)
                                            s3_url = s3_service.get_s3_object_url(task_id)
                                            await task_repo.aupdate_log_info(
                                                task_id,
                                                has_logs=True,
                                                log_s3_key=s3_key,
                                                log_s3_url=s3_url,
                                            )
                                            logger.info(
                                                f"[+] Task {task_id} uploaded logs to S3 after FAILURE: "
                                                f"{str(callback_error)[:100]}...[TRUNCATED]"
                                            )
                                        else:
                                            logger.error(
                                                f"[x] {ErrorCodeEnum.UNEXPECTED_ERROR}: Failed to upload "
                                                f"logs to S3 for task {task_id} after "
                                                f"FAILURE: {callback_error} "
                                            )
                                            # Trigger the retry logic below
                                            raise callback_error
                                    else:
                                        await task_repo.aupdate_log_info(
                                            task_id,
                                            has_logs=False,
                                        )
                                    await task_repo.aupdate_task_status(
                                        task_id,
                                        status=TaskStatusEnum.FAILED,
                                        error=f"{ErrorCodeEnum.UNEXPECTED_ERROR}: {str(callback_error)}",
                                    )
                                    logger.error(
                                        f"[x] {ErrorCodeEnum.UNEXPECTED_ERROR}: Task "
                                        f"{task_id} FAILED: {callback_error}"
                                    )
                                    # Trigger the retry logic below
                                    raise callback_error

                    except (ConnectionError, DatabaseError) as infra_error:
                        # Infrastructure error - do not mark task as FAILED
                        # Requeue and assign to another worker node to retry later
                        logger.error(f"[x] Infrastructure error for task_id={task_id}: {infra_error}")
                        # Route to `dead-letter queue` for later processing
                        if retry_count == MAX_RETRIES:
                            logger.error(
                                f"[x] {ErrorCodeEnum.MAX_RETRIES_EXCEEDED}: Infrastructure "
                                f"error persisted after {MAX_RETRIES} |"
                                f"attempts for task_id={task_id}. Routing message to dead-letter queue."
                            )
                            # Raise to trigger message requeue
                            raise infra_error

                except (json.JSONDecodeError, UnicodeDecodeError) as poison_err:
                    # Poison message: Log and ACK to remove from queue
                    logger.error(
                        f"{ErrorCodeEnum.POISON_MESSAGE}: Dropping poison message with "
                        f"task_id={task_id}, "
                        f"correlation_id={message.correlation_id}: {poison_err}"
                    )
                    # Do not retry poison errors. Acknowledge to remove from queue.
                    await message.ack()

                except Exception:
                    # Stateless retry logic
                    # Infra error is propagated here to trigger retry

                    # Record failure
                    self.breaker.record_failure()

                    if retry_count < MAX_RETRIES:
                        logger.warning(
                            f"[x] Error processing task_id={task_id}. \n"
                            f"Attempt {retry_count + 1}/{MAX_RETRIES}. "
                            f"CircuitBreaker state '{self.breaker.state}'. "
                            f"failure_count={self.breaker.failure_count}/{self.breaker.failure_threshold}. \n"
                            f"Retrying via the Wait Queue..."
                        )
                        retry_headers = headers.copy()
                        retry_headers["x-retry-count"] = retry_count + 1
                        retry_headers["x-circuit-breaker-state"] = self.breaker.state
                        retry_headers["x-circuit-breaker-failures"] = str(self.breaker.failure_count)

                        # Re-publish to delay queue for retry after TTL
                        await producer.apublish(
                            message=json.loads(message.body.decode()),
                            queue_name=delay_queue_name,
                            priority=PriorityEnum.MEDIUM,
                            headers=retry_headers,
                            request_id=correlation_id,
                            task_id=task_id,
                            durable=durable,
                        )
                        # Acknowledge current message to remove from queue
                        await message.ack()
                    else:
                        logger.error(
                            f"[x] {ErrorCodeEnum.MAX_RETRIES_EXCEEDED}: Exhausted all retries for "
                            f"task_id={task_id}. CircuitBreaker state '{self.breaker.state}'. "
                            "\nSending to dead-letter queue."
                        )
                        # Update task status to FAILED before sending to DLQ
                        # Re-using existing DB session from DB pool
                        try:
                            async with aget_db_session() as db:
                                repo = TaskRepository(db)
                                await repo.aupdate_task_status(
                                    task_id,
                                    status=TaskStatusEnum.FAILED,
                                    error=f"{ErrorCodeEnum.MAX_RETRIES_EXCEEDED}: Exhausted all retry "
                                    f"attempts. CircuitBreaker state '{self.breaker.state}'.",
                                )
                                await repo.aupdate_dlq_status(task_id, in_dlq=True)
                        except Exception as db_error:
                            logger.error(
                                f"[x] {ErrorCodeEnum.DATABASE_ERROR}: Failed to update "
                                f"task status for {task_id}: {db_error}"
                            )
                        finally:
                            # Reject with requeue=False to route via DLX to DLQ
                            await message.nack(requeue=False)


# ================ Example Usage ================
async def process_data_chunk(chunk_id: int, data: dict[str, Any]) -> None:  # noqa: ARG001
    """Simulates a nested function to test if child logs are captured."""
    logger.debug(f"  [Sub-Task] Starting sub-chunk {chunk_id} processing...")
    await asyncio.sleep(0.6)

    if random.random() < 0.1:  # 10% chance to log a warning
        logger.warning(f"  [Sub-Task] Chunk {chunk_id} detected high memory pressure.")

    logger.debug(f"  [Sub-Task] Chunk {chunk_id} successfully transformed.")


@queue_result_on_completion(queue_name="task_queue_results")
async def example_consumer_callback(message: dict[str, Any]) -> dict[str, Any]:
    """
    Complex callback to test multi-level logging capture and S3 upload.
    """
    start_time = time.perf_counter()
    task_data = message

    logger.info("🚀 Starting high-complexity execution...")
    logger.info(f"Target Payload: {json.dumps(task_data, indent=2)[:70]}...[TRUNCATED]")

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
        sleep_duration = random.uniform(0.5, 10)
        await asyncio.sleep(sleep_duration)
        # Randomly simulate an 'insight' for the log
        confidence = random.uniform(0.85, 0.99)
        logger.info(f"🤖 AI Inference complete. Confidence Score: {confidence:.2%}")

        # Summary
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.info(f"🏁 Task execution finished successfully in {duration:.2f} seconds.")
        return {
            "status": "success",
            "result": {k: f"{v}" * 2 for k, v in task_data.items()},
        }

    except Exception as e:
        logger.exception(f"💥 Fatal error during callback execution: {str(e)}")
        raise  # Re-raise to let the consumer handle the FAILED status update


async def run_worker(callback: CONSUMER_CALLBACK_FN) -> None:
    """Run the RabbitMQ consumer worker with graceful shutdown.

    Parameters
    ----------
    callback : Callable[[dict[str, Any]], Awaitable[None]]
        The async callback function to process each consumed message.

    Returns
    -------
    None
    """
    from src.config import app_config

    consumer = RabbitMQConsumer(app_config)

    # Setup signal handlers for graceful shutdown
    def signal_handler(sig: int) -> None:
        """Handle shutdown signals. This sets the shutdown event to stop consuming.

        Parameters
        ----------
        sig : int
            The signal number received.
        """
        logger.info(f"[+] Received signal {sig}, initiating graceful shutdown...")
        consumer._shutdown_event.set()

    # Register signal handlers
    loop = asyncio.get_event_loop()
    # Ctrl+C and termination signals
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    try:
        # --------------- Start Consuming Messages ---------------
        # Consume with async callback using context manager
        async with consumer.aconnection_context():
            # Create consume task for consuming messages from task queue and processing via callback
            consume_task = asyncio.create_task(
                consumer.consume(
                    queue_name=app_config.rabbitmq_config.queue_names.task_queue,
                    callback=callback,
                    durable=True,  # Queue survives broker restarts
                )
            )

            # Wait/block the event loop for shutdown event. If a shutdown signal is NOT received, this will
            # run indefinitely keeping the consumer alive to process messages.
            await consumer._shutdown_event.wait()

            # --------------- Graceful Shutdown ---------------
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


async def main(callback: CONSUMER_CALLBACK_FN) -> None:
    """Entry point to run the consumer with S3 bucket check.

    Parameters
    ----------
    callback : Callable[[dict[str, Any]], Awaitable[None]]
        The async callback function to process each consumed message.

    Returns
    -------
    None
    """
    s3_service = S3StorageService()
    if not await s3_service.acheck_bucket_exists():
        logger.error("[x] S3 bucket not accessible")
        sys.exit(1)
    logger.info("[+] S3 bucket is accessible, starting consumer...")

    await run_worker(callback)


if __name__ == "__main__":
    try:
        asyncio.run(main(callback=example_consumer_callback))
        logger.info("[+] Exiting gracefully")

    except KeyboardInterrupt:
        logger.info("[+] Received KeyboardInterrupt, exiting...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"[x] Fatal error running consumer: {e}")
        sys.exit(1)
