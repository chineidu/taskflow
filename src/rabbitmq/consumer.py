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

import aio_pika
from sqlalchemy.exc import DatabaseError

from src import add_file_handler, create_logger
from src.config import app_config, app_settings
from src.db.models import aget_db_session
from src.db.repositories.task_repository import TaskRepository
from src.rabbitmq.base import BaseRabbitMQ
from src.rabbitmq.producer import RabbitMQProducer
from src.rabbitmq.utilities import map_priority_enum_to_int
from src.schemas.rabbitmq.base import QueueArguments
from src.schemas.types import ErrorCodeEnum, PriorityEnum, TaskEventTypeEnum, TaskStatusEnum
from src.services.storage import S3StorageLogUploadPolicy, S3StorageService
from src.utilities import CircuitBreaker

if TYPE_CHECKING:
    from src.config.config import AppConfig

logger = create_logger("rabbitmq.consumer", structured=True)
RABBITMQ_URL: str = app_settings.rabbitmq_url
MAX_RETRIES: int = app_config.rabbitmq_config.max_retries
DELAY_BETWEEN_RETRIES: int = app_config.rabbitmq_config.retry_backoff_delay
SYSTEM_FAILURE_THRESHOLD: int = app_config.rabbitmq_config.circuit_breaker_config.failure_threshold
SYSTEM_RECOVERY_TIMEOUT: int = app_config.rabbitmq_config.circuit_breaker_config.recovery_timeout

# Takes a [deserialized message dict and a callback] and returns [Any]
type CONSUMER_CALLBACK_FN = Callable[
    [
        dict[str, Any],
        # Callback to report progress percentage
        Callable[[int], Awaitable[None] | None],
    ],
    Awaitable[Any] | Any,
]


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
        result_queue_name: str | None = None,
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
        result_queue_name : str | None, optional
            Name of the queue to publish results to upon successful completion.
        """
        # --------------- Infrastructure Setup ---------------
        await self.aconnect()
        producer = RabbitMQProducer(self.config)  # Used for re-publishing retries
        await producer.aconnect()  # Ensure producer is connected before use

        s3_log_policy = S3StorageLogUploadPolicy(
            storage_service=S3StorageService(),
            max_attempts=2,
            base_delay=1.0,
            raise_on_failure=False,  # Not critical to fail the task if logs can't be uploaded
        )
        progress_topic_name = app_config.rabbitmq_config.topic_names.progress_topic
        priority = app_config.rabbitmq_config.queue_priority
        priority_int = map_priority_enum_to_int(priority)

        assert self.channel is not None, "Channel is not established."

        await self.aensure_dlq(
            dlq_name=self.config.rabbitmq_config.dlq_config.dlq_name,
            dlx_name=self.config.rabbitmq_config.dlq_config.dlx_name,
        )

        # --------------- Setup queues ---------------
        if result_queue_name:
            await self.aensure_queue(
                queue_name=result_queue_name,
                arguments=QueueArguments(
                    x_dead_letter_exchange=None,  # No DLX for result queue
                    x_max_priority=priority_int,
                ),
                durable=True,
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
        # Progress queue
        progress_exchange = await self.aensure_topic(topic_name=progress_topic_name, durable=True)

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

                # ----------- Helper functions for progress and event reporting -----------
                async def aprogress_reporter(
                    percentage: int,
                    _task_id: str = task_id,
                    _correlation_id: str = correlation_id,
                ) -> None:
                    """Report task progress by publishing progress updates to the progress exchange."""
                    event_data = {
                        "task_id": _task_id,
                        "correlation_id": _correlation_id,
                        "progress": percentage,
                        "status": "processing",
                    }
                    event_msg = aio_pika.Message(
                        body=json.dumps(event_data).encode(),
                        content_type="application/json",
                        priority=map_priority_enum_to_int(priority),
                        correlation_id=_correlation_id,
                        headers={"task_id": _task_id, "event_type": TaskEventTypeEnum.TASK_PROGRESS},
                    )
                    try:
                        await progress_exchange.publish(
                            message=event_msg,
                            routing_key=f"{TaskEventTypeEnum.TASK_PROGRESS}.{_task_id}",
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to publish progress event for task {_task_id}: {e}", exc_info=True
                        )

                def progress_reporter(
                    percentage: int,
                    _task_id: str = task_id,
                    _correlation_id: str = correlation_id,
                ) -> None:
                    """Report task progress from sync functions by scheduling async
                    publish. This function blocks the worker thread briefly.

                    For a fire-and-forget approach, consider tweaking the implementation here.
                    """
                    # Schedule the coroutine on the main loop and block
                    # this thread until done
                    loop = asyncio.get_running_loop()
                    future = asyncio.run_coroutine_threadsafe(
                        aprogress_reporter(percentage, _task_id, _correlation_id), loop
                    )
                    try:
                        # Blocks the worker thread (not main event loop)
                        future.result(timeout=2.0)
                    except TimeoutError:
                        logger.warning(f"[progress] timeout task_id={_task_id} progress={percentage}")
                    except Exception as e:
                        logger.error(
                            f"[-] Failed to report progress for task_id={_task_id}: {e} progress={percentage}"
                        )

                async def apublish_task_event(
                    event_type: TaskEventTypeEnum,
                    status: TaskStatusEnum,
                    message: str | None = None,
                    error: str | None = None,
                    _task_id: str = task_id,
                    _correlation_id: str = correlation_id,
                ) -> None:
                    """Publish task events such as failure or completion, etc."""
                    routing_key: str = f"{event_type.value}.{_task_id}"
                    event_data: dict[str, Any] = {
                        "task_id": _task_id,
                        "correlation_id": _correlation_id,
                        "status": status if isinstance(status, str) else status.value,
                        "timestamp": datetime.now().isoformat(),
                    }

                    if message:
                        event_data["message"] = message
                    if error:
                        event_data["error"] = error

                    event_msg = aio_pika.Message(
                        body=json.dumps(event_data).encode(),
                        content_type="application/json",
                        priority=map_priority_enum_to_int(priority),
                        correlation_id=_correlation_id,
                        headers={
                            "task_id": _task_id,
                            "event_type": event_type.value,
                        },
                    )

                    try:
                        await progress_exchange.publish(
                            message=event_msg,
                            routing_key=(routing_key),
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to publish {event_type.value} event for task {_task_id}: {e}",
                            exc_info=True,
                        )

                # ----------- Begin Processing Logic -----------
                msg = (
                    f"[*] Handling message | task_id={task_id} | correlation_id={correlation_id} "
                    f"| time={timestamp} | retry_count={retry_count}"
                )
                logger.info(msg)

                try:
                    await apublish_task_event(
                        event_type=TaskEventTypeEnum.TASK_CREATED,
                        status=TaskStatusEnum.IN_PROGRESS,
                        message=msg,
                        _task_id=task_id,
                        _correlation_id=correlation_id,
                    )
                except Exception as e:
                    logger.error(f"Failed to publish TASK_CREATED event: {e}", exc_info=True)

                # --------------- Circuit Breaker Check ---------------
                # If circuit breaker is OPEN, route message to delay queue without processing
                if not self.breaker.can_execute():
                    logger.warning(
                        f"⚠️ Circuit Breaker is ({self.breaker.state.upper()}). "
                        f"Skipping message processing and moving message to '{delay_queue_name}'."
                    )
                    retry_headers = headers.copy()
                    # Update circuit breaker state in headers for downstream consumers
                    retry_headers["x-retry-count"] = retry_count + 1
                    retry_headers["x-circuit-breaker"] = self.breaker.state

                    if retry_count + 1 >= MAX_RETRIES:
                        error_msg = (
                            f"❌ {ErrorCodeEnum.MAX_RETRIES_EXCEEDED}: Message task_id={task_id} "
                            f"exceeded max retries while circuit breaker is {self.breaker.state.upper()}. "
                            f"Routing to dead-letter queue."
                        )
                        logger.error(error_msg)
                        try:
                            # Update task status to FAILED before sending to DLQ
                            async with aget_db_session() as db:
                                task_repo = TaskRepository(db)
                                await task_repo.aupdate_task_status(
                                    task_id,
                                    status=TaskStatusEnum.FAILED,
                                    error=f"{ErrorCodeEnum.MAX_RETRIES_EXCEEDED}: "
                                    f"Exceeded max retries while circuit breaker is "
                                    f"{self.breaker.state.upper()}.",
                                )
                                await task_repo.aupdate_dlq_status(task_id, in_dlq=True)
                        except Exception as db_error:
                            logger.error(
                                f"[-] {ErrorCodeEnum.DATABASE_ERROR}: Failed to update "
                                f"task status for {task_id}: {db_error}"
                            )
                        finally:
                            # Publish failure event before marking FAILED
                            await apublish_task_event(
                                event_type=TaskEventTypeEnum.TASK_ROUTED_TO_DELAY_QUEUE,
                                status=TaskStatusEnum.PENDING,
                                error=error_msg,
                                _task_id=task_id,
                                _correlation_id=correlation_id,
                            )
                            # Reject with requeue=False to route via DLX to DLQ
                            await message.nack(requeue=False)
                    else:
                        error_msg = (
                            f"⚠️ Circuit breaker is {self.breaker.state.upper()}. Routing message "
                            f"task_id={task_id} to delay queue '{delay_queue_name}' for retry."
                        )
                        # Re-publish to delay queue for retry after TTL
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
                                f"⚠️ Message task_id={task_id} routed to '{delay_queue_name}' due "
                                f"to {self.breaker.state.upper()} circuit breaker"
                            )
                            # Acknowledge current message to remove from queue
                            await message.ack()
                            continue
                        logger.error(
                            f"[-] {ErrorCodeEnum.RABBITMQ_ERROR}: Failed to route message task_id={task_id} "
                            f"to '{delay_queue_name}' during {self.breaker.state.upper()} state"
                        )

                        await apublish_task_event(
                            event_type=TaskEventTypeEnum.TASK_ROUTED_TO_DELAY_QUEUE,
                            status=TaskStatusEnum.PENDING,
                            error=error_msg,
                            _task_id=task_id,
                            _correlation_id=correlation_id,
                        )
                        # Do not ack, let it be retried later
                        continue

                # --------------- Core Processing ---------------
                try:
                    msg = (
                        f"[*] Processing task_id={task_id} | Attempt {retry_count}/{MAX_RETRIES} "
                        f"failure_count={self.breaker.failure_count}/{self.breaker.failure_threshold}. \n"
                    )
                    logger.info(msg)
                    await apublish_task_event(
                        event_type=TaskEventTypeEnum.TASK_STARTED,
                        status=TaskStatusEnum.IN_PROGRESS,
                        error=msg,
                        _task_id=task_id,
                        _correlation_id=correlation_id,
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

                                # Business logic execution happens here
                                try:
                                    # ----------- Task timeout enforcement -----------
                                    execution_result = None
                                    if asyncio.iscoroutinefunction(callback):
                                        execution_result = await asyncio.wait_for(
                                            callback(message_dict, aprogress_reporter),
                                            timeout=self.config.rabbitmq_config.tasks_timeout,
                                        )
                                    else:
                                        # Convert sync function to async and run with timeout
                                        _async_version = asyncio.to_thread(
                                            callback, message_dict, progress_reporter
                                        )
                                        execution_result = await asyncio.wait_for(
                                            _async_version, timeout=self.config.rabbitmq_config.tasks_timeout
                                        )

                                    logger.removeHandler(task_handler)
                                    # Stop logging and save the file.
                                    task_handler.close()
                                    # Upload logs to S3 if file has content
                                    if temp_path.stat().st_size > 0:
                                        upload_result = await s3_log_policy.aupload_with_retry(
                                            filepath=temp_path,
                                            task_id=task_id,
                                            correlation_id=correlation_id,
                                            environment=app_settings.ENV,
                                        )
                                        if not upload_result.error:
                                            # Update logs info in DB
                                            s3_key = upload_result.s3_key
                                            s3_url = upload_result.s3_url
                                            await task_repo.aupdate_log_info(
                                                task_id,
                                                has_logs=True,
                                                log_s3_key=s3_key,
                                                log_s3_url=s3_url,
                                            )
                                            # Publish result if enabled (reuses persistent connection)
                                            if result_queue_name and execution_result is not None:
                                                # Use standard format if raw generic result, else use as is
                                                payload: dict[str, Any] = (
                                                    execution_result
                                                    if isinstance(execution_result, dict)
                                                    else {"result": execution_result}
                                                )
                                                await producer.apublish(
                                                    message=payload,
                                                    queue_name=result_queue_name,
                                                    priority=PriorityEnum.MEDIUM,
                                                    task_id=task_id,
                                                    request_id=correlation_id,
                                                    durable=durable,
                                                    headers={"event_type": "task.completed"},
                                                )
                                                logger.info(
                                                    f"[+] Completion event published to "
                                                    f"'{result_queue_name}': task_id={task_id}"
                                                )
                                        else:
                                            logger.error(
                                                f"{ErrorCodeEnum.BLOB_STORAGE_ERROR}: [x] Failed to upload "
                                                f"logs to S3 for task {task_id}"
                                            )
                                            await task_repo.aupdate_log_info(
                                                task_id,
                                                has_logs=False,
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
                                    msg = f"[+] Task {task_id} COMPLETED successfully"
                                    logger.info(msg)

                                    # Publish completion event before marking COMPLETED
                                    await apublish_task_event(
                                        event_type=TaskEventTypeEnum.TASK_COMPLETED,
                                        status=TaskStatusEnum.COMPLETED,
                                        message=msg,
                                        _task_id=task_id,
                                        _correlation_id=correlation_id,
                                    )

                                except (ValueError, KeyError, TypeError) as biz_logic_err:
                                    # Business logic error inside callback. These errors are not retried
                                    # because they can't be fixed until manual fix is done
                                    error_msg = f"{ErrorCodeEnum.BUSINESS_LOGIC_ERROR}: {str(biz_logic_err)}"
                                    logger.error(f"[x] {error_msg} in task {task_id}")

                                    # Publish failure event before marking FAILED
                                    await apublish_task_event(
                                        event_type=TaskEventTypeEnum.TASK_FAILED,
                                        status=TaskStatusEnum.FAILED,
                                        error=error_msg,
                                        _task_id=task_id,
                                        _correlation_id=correlation_id,
                                    )
                                    # Cleanup logging handler and upload the logs collected so far
                                    logger.removeHandler(task_handler)
                                    task_handler.close()

                                    if temp_path.exists() and temp_path.stat().st_size > 0:
                                        upload_result = await s3_log_policy.aupload_with_retry(
                                            filepath=temp_path,
                                            task_id=task_id,
                                            correlation_id=correlation_id,
                                            environment=app_settings.ENV,
                                        )
                                        if not upload_result.error:
                                            # Update logs info in DB
                                            s3_key = upload_result.s3_key
                                            s3_url = upload_result.s3_url
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
                                            await task_repo.aupdate_log_info(
                                                task_id,
                                                has_logs=False,
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
                                    error_msg = (
                                        f"{ErrorCodeEnum.TIMEOUT_ERROR}: Task {task_id} timed out "
                                        f"after {self.config.rabbitmq_config.tasks_timeout} seconds"
                                    )
                                    logger.error(f"[x] {error_msg} after timeout")

                                    # Publish failure event before marking FAILED
                                    await apublish_task_event(
                                        event_type=TaskEventTypeEnum.TASK_FAILED,
                                        status=TaskStatusEnum.FAILED,
                                        error=error_msg,
                                        _task_id=task_id,
                                        _correlation_id=correlation_id,
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
                                        upload_result = await s3_log_policy.aupload_with_retry(
                                            filepath=temp_path,
                                            task_id=task_id,
                                            correlation_id=correlation_id,
                                            environment=app_settings.ENV,
                                        )
                                        if not upload_result.error:
                                            # Update logs info in DB
                                            s3_key = upload_result.s3_key
                                            s3_url = upload_result.s3_url
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
                                            await task_repo.aupdate_log_info(
                                                task_id,
                                                has_logs=False,
                                            )
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
                                    error_msg = (
                                        f"{ErrorCodeEnum.UNEXPECTED_ERROR}: Task {task_id} FAILED due to "
                                        f"unexpected error: {str(callback_error)}"
                                    )
                                    logger.error(f"[x] {error_msg} in task {task_id}")

                                    # Publish failure event before marking FAILED
                                    await apublish_task_event(
                                        event_type=TaskEventTypeEnum.TASK_FAILED,
                                        status=TaskStatusEnum.FAILED,
                                        error=error_msg,
                                        _task_id=task_id,
                                        _correlation_id=correlation_id,
                                    )

                    except (ConnectionError, DatabaseError) as infra_error:
                        # Infrastructure error - do not mark task as FAILED
                        # Requeue and assign to another worker node to retry later
                        logger.error(f"[x] Infrastructure error for task_id={task_id}: {infra_error}")
                        # Route to `dead-letter queue` for later processing
                        if retry_count == MAX_RETRIES:
                            error_msg = (
                                f"❌ {ErrorCodeEnum.MAX_RETRIES_EXCEEDED}: Infrastructure error persisted "
                                f"after {MAX_RETRIES} attempts for task_id={task_id}. Routing message "
                                "to dead-letter queue."
                            )
                            logger.error(f"{error_msg}")
                            # Publish failure event before marking FAILED
                            await apublish_task_event(
                                event_type=TaskEventTypeEnum.TASK_FAILED,
                                status=TaskStatusEnum.FAILED,
                                error=error_msg,
                                _task_id=task_id,
                                _correlation_id=correlation_id,
                            )

                            # Raise to trigger routing to DLQ
                            raise infra_error

                except (json.JSONDecodeError, UnicodeDecodeError) as poison_err:
                    # Poison message: Log and ACK to remove from queue
                    error_msg = (
                        f"{ErrorCodeEnum.POISON_MESSAGE}: Poison message detected for "
                        f"task_id={task_id}: {poison_err}"
                    )
                    logger.error(f"[x] {error_msg} - Acknowledging to remove from queue.")

                    # Publish failure event before marking FAILED
                    await apublish_task_event(
                        event_type=TaskEventTypeEnum.TASK_FAILED,
                        status=TaskStatusEnum.FAILED,
                        error=error_msg,
                        _task_id=task_id,
                        _correlation_id=correlation_id,
                    )
                    # Do not retry poison errors. Acknowledge to remove from queue.
                    await message.ack()

                except Exception:
                    # Stateless retry logic
                    # Infra error is propagated here to trigger retry

                    # Record failure
                    self.breaker.record_failure()

                    if retry_count < MAX_RETRIES:
                        error_msg = (
                            f"[x] Error processing task_id={task_id}. \n"
                            f"Attempt {retry_count + 1}/{MAX_RETRIES}. "
                            f"CircuitBreaker state '{self.breaker.state}'. "
                            f"failure_count={self.breaker.failure_count}/{self.breaker.failure_threshold}. \n"
                            f"Retrying via the Wait Queue..."
                        )
                        logger.warning(error_msg)
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

                        # Publish failure event before marking FAILED
                        await apublish_task_event(
                            event_type=TaskEventTypeEnum.TASK_FAILED,
                            status=TaskStatusEnum.FAILED,
                            error=error_msg,
                            _task_id=task_id,
                            _correlation_id=correlation_id,
                        )
                        # Acknowledge current message to remove from queue
                        await message.ack()
                    else:
                        error_msg = (
                            f"❌ {ErrorCodeEnum.MAX_RETRIES_EXCEEDED}: Exhausted all retries for "
                            f"task_id={task_id}. CircuitBreaker state '{self.breaker.state}'. "
                            "\nSending to dead-letter queue."
                        )
                        logger.error(error_msg)
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
                            # Publish failure event before marking FAILED
                            await apublish_task_event(
                                event_type=TaskEventTypeEnum.TASK_FAILED,
                                status=TaskStatusEnum.FAILED,
                                error=error_msg,
                                _task_id=task_id,
                                _correlation_id=correlation_id,
                            )
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


async def example_consumer_callback(
    message: dict[str, Any], report_progress: Callable[[int], Awaitable[None] | None] | None = None
) -> dict[str, Any]:
    """
    Complex callback to test multi-level logging capture and S3 upload.
    """
    start_time = time.perf_counter()
    task_data = message

    logger.info("🚀 Starting high-complexity execution...")
    logger.info(f"Target Payload: {json.dumps(task_data, indent=2)[:70]}...[TRUNCATED]")

    try:
        if report_progress:
            result = report_progress(5)  # 5% (Started)
            if result is not None and asyncio.iscoroutine(result):
                await result
        # Phase 1: Validation
        logger.info("Phase 1: Validating input schema...")
        if not task_data:
            logger.error("❌ Validation Failed: Empty payload received.")
            raise ValueError("Empty Payload")
        await asyncio.sleep(0.3)
        logger.info("✅ Validation complete.")

        if report_progress:
            result = report_progress(20)  # 20%
            if result is not None and asyncio.iscoroutine(result):
                await result
        # Phase 2: Transformation (Nested Logging)
        logger.info("Phase 2: Processing data chunks...")
        for i in range(1, 4):
            await process_data_chunk(i, task_data)
            # Incremental progress update
            if report_progress:
                result = report_progress(20 + (i * 13))  # 20 -> 60%
                if result is not None and asyncio.iscoroutine(result):
                    await result
        logger.info("✅ All 3 chunks processed.")

        # Phase 3: External 'Service' simulation
        logger.info("Phase 3: Communicating with external AI service...")
        sleep_duration = random.uniform(0.5, 10)
        await asyncio.sleep(sleep_duration)
        # Randomly simulate an 'insight' for the log
        confidence = random.uniform(0.85, 0.99)
        logger.info(f"🤖 AI Inference complete. Confidence Score: {confidence:.2%}")
        if report_progress:
            result = report_progress(85)  # 85%
            if result is not None and asyncio.iscoroutine(result):
                await result

        # Summary
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.info(f"🏁 Task execution finished successfully in {duration:.2f} seconds.")
        if report_progress:
            result = report_progress(100)  # 100%
            if result is not None and asyncio.iscoroutine(result):
                await result
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
                    result_queue_name=app_config.rabbitmq_config.queue_names.result_queue,
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
