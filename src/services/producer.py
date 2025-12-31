from uuid import uuid4

from src.config import app_config
from src.rabbitmq.producer import RabbitMQProducer
from src.schemas.rabbitmq.payload import RabbitMQPayload, SubmittedJobResult


async def atrigger_job(
    messages: list[RabbitMQPayload],
    queue_name: str,
    routing_key: str | None = None,
) -> SubmittedJobResult:
    """Trigger a job by publishing a message to the specified RabbitMQ queue.

    Parameters
    ----------
    messages : list[RabbitMQPayload]
        The message payload to be sent.
    queue_name : str
        The name of the RabbitMQ queue to publish the message to.
    routing_key : str, optional
        The routing key for the message, by default None.

    Returns
    -------
    SubmittedJobResult
        An instance containing the task ID and number of messages published.
    """
    task_id = str(uuid4())
    producer = RabbitMQProducer(config=app_config)

    async with producer.aconnection_context():
        await producer.aensure_queue(queue_name=queue_name, durable=True)
        num_msgs: int = await producer.abatch_publish(
            messages=messages,
            queue_name=queue_name,
            correlation_id=task_id,
            routing_key=routing_key,
        )

    return SubmittedJobResult(task_id=task_id, number_of_messages=num_msgs)
