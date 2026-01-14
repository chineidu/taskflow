import asyncio
from datetime import datetime
from functools import wraps
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar, overload

from src import create_logger
from src.config.config import app_config
from src.rabbitmq.producer import RabbitMQProducer
from src.schemas.rabbitmq.base import QueueArguments, SystemHealthResult
from src.schemas.types import PriorityEnum

logger = create_logger("rabbitmq.utilities")

# --------- Decorator for automatic queue publishing on function completion --------- #
P = ParamSpec("P")

# Separate type vars for sync and async functions
SyncFunc = TypeVar("SyncFunc", bound=Callable[..., Any])
AsyncFunc = TypeVar("AsyncFunc", bound=Callable[..., Awaitable[Any]])


@overload
def queue_result_on_completion(
    func: SyncFunc,
    *,
    queue_name: str | None = None,
    dlq_name: str | None = None,
    task_id_key: str | None = None,
) -> SyncFunc:
    # Called without parameters. e.g. @queue_result_on_completion
    ...


@overload
def queue_result_on_completion(
    func: AsyncFunc,
    *,
    queue_name: str | None = None,
    dlq_name: str | None = None,
    task_id_key: str | None = None,
) -> AsyncFunc:
    # Called without parameters. e.g. @queue_result_on_completion
    ...


@overload
def queue_result_on_completion(
    func: None = None,
    *,
    queue_name: str | None = None,
    dlq_name: str | None = None,
    task_id_key: str | None = None,
) -> Callable[[AsyncFunc], AsyncFunc]:
    ...
    # Called with parameters. e.g. @queue_result_on_completion(queue_name="results_queue")


@overload
def queue_result_on_completion(
    func: None = None,
    *,
    queue_name: str | None = None,
    dlq_name: str | None = None,
    task_id_key: str | None = None,
) -> Callable[[SyncFunc], SyncFunc]:
    ...
    # Called with parameters. e.g. @queue_result_on_completion(queue_name="results_queue")


def queue_result_on_completion(
    func: Callable[..., Any] | None = None,
    *,
    queue_name: str | None = None,
    dlq_name: str | None = None,
    task_id_key: str | None = None,
    priority: PriorityEnum | str = PriorityEnum.MEDIUM,
) -> Callable[..., Any]:
    """Decorator to automatically publish function results to a RabbitMQ queue.

    Parameters
    ----------
    queue_name : str | None, optional
        The name of the RabbitMQ queue to publish results to.
    dlq_name : str | None, optional
        The name of the Dead Letter Queue (DLQ) for error handling. If not provided, defaults to
        '{queue_name}_dlq'.
    task_id_key : str | None, optional
        The key in the result dict to use as the task ID. If not provided, defaults to None.
    priority : PriorityEnum
        The priority level of the message.

    Returns
    -------
    Callable[[Callable[P, Any]], Callable[P, Any]]
        A decorator that wraps the target function to publish its result or error to the specified queues.

    Raises
    ------
    Exception
        Re-raises any exception from the decorated function after publishing to DLQ.

    Examples
    --------
    Async function example:
    >>> @queue_result_on_completion(queue_name="results_queue")
    >>> async def process_task(data: dict[str, Any]) -> dict[str, Any]:
    ...     result = await some_processing(data)
    ...     return {"task_id": "123", "status": "completed", "result": result}

    Sync function example:
    >>> @queue_result_on_completion(queue_name="results_queue", dlq_name="errors_queue")
    >>> def sync_process(data: dict[str, Any]) -> dict[str, Any]:
    ...     return {"task_id": "456", "status": "processed"}
    """

    queue_name = queue_name if queue_name else "default_queue"
    dlq_name = dlq_name if dlq_name else f"{queue_name}_dlq"
    queue_args = QueueArguments(x_dead_letter_exchange=None, x_max_priority=None)

    def decorator(f: Callable[P, Any]) -> Callable[P, Any]:
        if asyncio.iscoroutinefunction(f):

            @wraps(f)
            async def awrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                """Wrapper for async functions."""
                producer = RabbitMQProducer(config=app_config)

                try:
                    # Execute the function
                    result = await f(*args, **kwargs)
                    payload = result if isinstance(result, dict) else {"result": result}
                    timestamp = datetime.now().isoformat()
                    headers: dict[str, Any] = {"timestamp": timestamp, "task_id": task_id_key}

                    # Publish the result to the specified queue
                    async with producer.aconnection_context():
                        await producer.aensure_queue(
                            queue_name=queue_name, arguments=queue_args, durable=True
                        )

                        success, task_id = await producer.apublish(
                            message=payload,
                            queue_name=queue_name,
                            priority=priority,
                            task_id=task_id_key,
                            durable=True,
                            headers=headers
                        )
                        logger.info(f"[+] Published result to '{queue_name}': task_id={task_id}")
                    return result

                except Exception as e:
                    logger.error(f"[x] Function '{f.__name__}' failed: {str(e)}")

                    # Publish error to DLQ
                    async with producer.aconnection_context():
                        await producer.aensure_queue(queue_name=dlq_name, arguments=queue_args, durable=True)

                        error_payload = {
                            "error": str(e),
                            "function_name": f.__name__,
                            "exception_type": type(e).__name__,
                        }
                        timestamp = datetime.now().isoformat()
                        headers: dict[str, Any] = {"timestamp": timestamp, "task_id": task_id_key}

                        await producer.apublish(
                            message=error_payload,
                            queue_name=dlq_name,
                            priority=priority,
                            task_id="error_unknown",
                            headers=headers
                        )
                        logger.error(f"[x] Published error to DLQ '{dlq_name}'")
                    raise

            return awrapper

        # Otherwise, it's a sync function
        @wraps(f)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            """Wrapper for sync functions."""
            producer = RabbitMQProducer(config=app_config)

            try:
                # Execute the function
                result = f(*args, **kwargs)
                payload = result if isinstance(result, dict) else {"result": result}
                timestamp = datetime.now().isoformat()
                headers: dict[str, Any] = {"timestamp": timestamp, "task_id": task_id_key}

                # Publish the result to the specified queue
                async def _publish_success() -> None:
                    async with producer.aconnection_context():
                        await producer.aensure_queue(
                            queue_name=queue_name, arguments=queue_args, durable=True
                        )

                        success, task_id = await producer.apublish(
                            message=payload,
                            queue_name=queue_name,
                            priority=priority,
                            task_id=task_id_key,
                            headers=headers,
                            durable=True,
                        )
                        logger.info(f"[+] Published result to '{queue_name}': task_id={task_id}")

                # Run the async publish in the event loop
                asyncio.run(_publish_success())
                return result

            except Exception as e:
                logger.error(f"[x] Function '{f.__name__}' failed: {str(e)}")

                # Publish error to DLQ
                async def _publish_error(e: Exception) -> None:
                    async with producer.aconnection_context():
                        await producer.aensure_queue(queue_name=dlq_name, arguments=queue_args, durable=True)

                        error_payload = {
                            "error": str(e),
                            "function_name": f.__name__,
                            "exception_type": type(e).__name__,
                        }
                        timestamp = datetime.now().isoformat()
                        headers: dict[str, Any] = {"timestamp": timestamp, "task_id": task_id_key}

                        await producer.apublish(
                            message=error_payload,
                            priority=priority,
                            queue_name=dlq_name,
                            task_id="error_unknown",
                            headers=headers
                        )
                        logger.error(f"[x] Published error to DLQ '{dlq_name}'")

                asyncio.run(_publish_error(e))
                raise

        return wrapper

    # Called with parameters
    if func is None:
        return decorator

    # Called without parameters
    return decorator(func)


async def aget_system_health(producer: RabbitMQProducer | None = None) -> SystemHealthResult:
    """Get RabbitMQ system health metrics.

    Parameters
    ----------
    producer : RabbitMQProducer | None
        Optional producer instance. If None, creates a temporary one.

    Returns
    -------
    SystemHealthResult
        Object containing queue metrics.
    """
    queue_args = QueueArguments(
        x_dead_letter_exchange=app_config.rabbitmq_config.dlq_config.dlx_name, x_max_priority=10
    )
    if producer is None:
        producer = RabbitMQProducer(config=app_config)
        async with producer.aconnection_context():
            # Match the queue arguments used during consumer setup
            queue_info = await producer.aensure_queue(
                queue_name="task_queue",
                arguments=queue_args,
                durable=True,
            )
    else:
        queue_info = await producer.aensure_queue(
            queue_name="task_queue",
            arguments=queue_args,
            durable=True,
        )
    return SystemHealthResult(
        messages_ready=(queue_info.declaration_result.message_count or 0) if queue_info else 0,
        workers_online=(queue_info.declaration_result.consumer_count or 0) if queue_info else 0,
    )


def map_priority_enum_to_int(priority: PriorityEnum | str) -> int:
    """Convert PriorityEnum to its string representation.

    Parameters
    ----------
    priority : PriorityEnum | str
        The priority level as an enum or string.

    Returns
    -------
    str
        The string representation of the priority level.
    """
    valid_priorities = ", ".join({e.value for e in PriorityEnum})
    try:
        if isinstance(priority, str):
            priority = PriorityEnum(priority.lower())
        else:
            priority = priority

    except ValueError:
        raise ValueError(f"priority must be one of ({valid_priorities}), got '{priority}'") from None

    mapping: dict[PriorityEnum, int] = {
        PriorityEnum.LOW: 1,
        PriorityEnum.MEDIUM: 6,
        PriorityEnum.HIGH: 10,
    }
    # Default to MEDIUM if not found
    return mapping.get(priority, 6)
