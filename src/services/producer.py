import json
import time
from datetime import datetime
from typing import Any

from src import create_logger
from src.config import app_config
from src.db.models import aget_db_session
from src.db.repositories.task_repository import TaskRepository
from src.rabbitmq.base import BaseRabbitMQ
from src.rabbitmq.producer import RabbitMQProducer
from src.schemas.rabbitmq.payload import RabbitMQPayload, SubmittedJobResult

logger = create_logger(name="services.producer")


async def atrigger_job(
    messages: list[RabbitMQPayload],
    queue_name: str,
    request_id: str,
    routing_key: str | None = None,
    headers: dict[str, str] | None = None,
    producer: RabbitMQProducer | None = None,
) -> SubmittedJobResult:
    """Trigger a job by publishing a message to the specified RabbitMQ queue.

    Parameters
    ----------
    messages : list[RabbitMQPayload]
        The message payload to be sent.
    queue_name : str
        The name of the RabbitMQ queue to publish the message to.
    request_id : str
        The unique request identifier for tracking the job.
    headers : dict[str, str] | None, optional
        Optional headers to include with the message, by default None.
    routing_key : str, optional
        The routing key for the message, by default None.
    producer : RabbitMQProducer | None, optional
        An optional RabbitMQProducer instance. If not provided, a new instance will be created.

    Returns
    -------
    SubmittedJobResult
        An instance containing the task ID and number of messages published.
    """
    if not producer:
        producer = RabbitMQProducer(config=app_config)

        async with producer.aconnection_context():
            await producer.aensure_queue(
                queue_name=queue_name,
                arguments={
                    "x-dead-letter-exchange": app_config.rabbitmq_config.dlq_config.dlx_name,
                },
                durable=True,
            )
            num_msgs, task_ids = await producer.abatch_publish(
                messages=messages,
                queue_name=queue_name,
                request_id=request_id,
                routing_key=routing_key,
                headers=headers,
            )
    else:
        # Use existing producer connection
        await producer.aensure_queue(
            queue_name=queue_name,
            arguments={
                "x-dead-letter-exchange": app_config.rabbitmq_config.dlq_config.dlx_name,
            },
            durable=True,
        )
        num_msgs, task_ids = await producer.abatch_publish(
            messages=messages,
            queue_name=queue_name,
            request_id=request_id,
            routing_key=routing_key,
            headers=headers,
        )

    return SubmittedJobResult(task_ids=task_ids, number_of_messages=num_msgs)


async def areplay_dlq_messages(
    dlq_name: str, target_queue_name: str, max_messages: int = 100
) -> dict[str, Any]:
    """Replay messages from a Dead Letter Queue (DLQ) to the target queue.

    Parameters
    ----------
    dlq_name : str
        The name of the Dead Letter Queue to read messages from.
    target_queue_name : str
        The name of the target queue to which messages will be replayed.
    max_messages : int, optional
        The maximum number of messages to replay, by default 100.

    Returns
    -------
    dict[str, Any]
        Dictionary with 'success' and 'failed' counts of replayed messages.
    """
    client = BaseRabbitMQ(config=app_config)
    producer = RabbitMQProducer(config=app_config)

    success_count = 0
    failed_count = 0

    async with client.aconnection_context():
        # Ensure both queues exist
        # Use the same channel for all operations
        producer.channel = client.channel

        dlq_queue = await client.aensure_queue(queue_name=dlq_name, durable=True)
        await client.aensure_queue(
            queue_name=target_queue_name,
            arguments={"x-dead-letter-exchange": app_config.rabbitmq_config.dlq_config.dlx_name},
            durable=True,
        )

        # Fetch messages from DLQ one by one
        for _ in range(max_messages):
            message = await dlq_queue.get(timeout=1.0, fail=False)

            if message is None:
                # No more messages in DLQ
                break

            try:
                headers: dict[str, Any] = dict(message.headers) if message.headers else {}
                headers["replayed_at"] = datetime.now().isoformat()
                # Reset retry count on replay from DLQ
                headers["x-retry-count"] = 0

                # Republish to target queue preserving original message properties
                await producer.apublish(
                    message=json.loads(message.body.decode()),
                    queue_name=target_queue_name,
                    routing_key=None,
                    headers=headers,
                    task_id=str(message.headers.get("task_id")),
                )
                # Acknowledge only after successful publish
                await message.ack()
                success_count += 1

            except Exception:
                logger.error(f"[x] Failed to republish message from DLQ '{dlq_name}'")
                # Don't ack the message - leave it in DLQ for manual inspection
                await message.nack(requeue=True)
                failed_count += 1

    return {
        "success": success_count,
        "failed": failed_count,
        "status": "Replay successful" if success_count > 0 else "No messages replayed",
    }


async def areplay_dlq_message_by_task_id(
    dlq_name: str, target_queue_name: str, task_id: str, max_messages: int = 1000
) -> dict[str, int | str]:
    """Replay a specific message from a Dead Letter Queue (DLQ) to the target queue by task_id.

    Parameters
    ----------
    dlq_name : str
        The name of the Dead Letter Queue to read messages from.
    target_queue_name : str
        The name of the target queue to which messages will be replayed.
    task_id : str
        The task ID of the message to be replayed.
    max_messages : int, optional
        Maximum number of messages to search through, by default 1000.

    Returns
    -------
    dict[str, int | str]
        Dictionary indicating success status, task ID, and status message.
    """
    # Search the DB first (more efficient than searching RabbitMQ DLQ directly)
    async with aget_db_session() as db:
        repo = TaskRepository(db=db)
        task = await repo.aget_task_by_id(task_id=task_id)
        if not task or not task.in_dlq:
            logger.warning(f"[x] Task with ID={task_id} not found or not in DLQ")
            return {
                "success": False,
                "task_id": task_id,
                "status": "Task ID not found or not in DLQ",
            }
    # Proceed to search DLQ in RabbitMQ
    client = BaseRabbitMQ(config=app_config)
    producer = RabbitMQProducer(config=app_config)

    # ----- Temporary queue for searching -----
    # Instead of storing messages in memory (Python list), we use a temporary RabbitMQ queue
    # to hold messages that are not the target. This avoids high memory usage for large DLQs.
    # This also prevents requeueing a message that was just moved from the DLQ back to itself.
    # i.e. if the message is not the target, and we publish it back to the DLQ, it may be picked 
    # up again in the same search loop, causing an infinite loop.
    temp_queue_name = f"temp_search_queue_{task_id}_{int(time.time())}"
    found: bool = False

    async with client.aconnection_context():
        # Use the same channel for all operations
        producer.channel = client.channel

        # Ensure both queues exist
        dlq_queue = await client.aensure_queue(queue_name=dlq_name, durable=True)
        # Create temporary queue with auto-delete after 60 seconds of inactivity
        temp_queue = await client.aensure_queue(
            queue_name=temp_queue_name, durable=False, arguments={"x-expires": 60000}
        )
        await client.aensure_queue(
            queue_name=target_queue_name,
            arguments={"x-dead-letter-exchange": app_config.rabbitmq_config.dlq_config.dlx_name},
            durable=True,
        )

        # Search through DLQ messages to find the one with matching task_id
        for _ in range(max_messages):
            message = await dlq_queue.get(timeout=1.0, fail=False)

            if message is None:
                # No more messages in DLQ
                break

            message_task_id = str(message.headers.get("task_id")) if message.headers else None

            if message_task_id == task_id and not found:
                headers: dict[str, Any] = dict(message.headers) if message.headers else {}
                headers["replayed_at"] = datetime.now().isoformat()
                # Reset retry count on replay from DLQ
                headers["x-retry-count"] = 0

                # Found the target message - try to republish it
                try:
                    success, _ = await producer.apublish(
                        message=json.loads(message.body.decode()),
                        queue_name=target_queue_name,
                        routing_key=None,
                        headers=headers,
                        task_id=message_task_id,
                    )
                    # Acknowledge only after successful publish
                    await message.ack()
                    found = True
                    logger.info(f"[+] Replayed message with task_id={task_id} to '{target_queue_name}'")
                except Exception as e:
                    logger.error(f"[x] Failed to republish message with task_id={task_id}: {str(e)}")

            else:
                # Not the target message - save it to requeue later
                await producer.apublish(
                    message=json.loads(message.body.decode()),
                    queue_name=temp_queue.name,
                    routing_key=None,
                    durable=False,
                    headers=message.headers,
                    task_id=message_task_id,
                )
                # Acknowledge after successful publish to temp queue
                await message.ack()

        # Requeue all non-target messages back to the original DLQ
        logger.info(f"[~] Requeuing non-target messages back to DLQ '{dlq_name}'")
        while True:
            temp_message = await temp_queue.get(timeout=1.0, fail=False)
            if temp_message is None:
                # No more messages in temp queue
                break

            await producer.apublish(
                message=json.loads(temp_message.body.decode()),
                queue_name=dlq_name,
                routing_key=None,
                headers=temp_message.headers,
                task_id=str(temp_message.headers.get("task_id")) if temp_message.headers else None,
            )
            # Acknowledge after successful publish back to DLQ
            await temp_message.ack()

        return {
            "success": found,
            "task_id": task_id,
            "status": "Replay successful" if found else "Task ID not found in DLQ",
        }
