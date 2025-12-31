import json
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import aio_pika

from src import create_logger
from src.config import app_settings
from src.rabbitmq.base import BaseRabbitMQ
from src.schemas.rabbitmq.payload import RabbitMQPayload

if TYPE_CHECKING:
    from src.config.config import AppConfig


logger = create_logger("producer")
RABBITMQ_URL: str = app_settings.rabbitmq_url


class RabbitMQProducer(BaseRabbitMQ):
    """RabbitMQ message producer with connection management and error handling."""

    def __init__(self, config: "AppConfig", url: str = RABBITMQ_URL) -> None:
        """Initialize the producer with configuration.

        Parameters
        ----------
        config : AppConfig
            Application configuration containing RabbitMQ settings.
        url : str
            RabbitMQ connection URL.


        Returns
        -------
        None
        """
        # Initialize the base class
        super().__init__(config, url)

    async def apublish(
        self,
        message: RabbitMQPayload,
        queue_name: str,
        routing_key: str | None = None,
        request_id: str | None = None,
        durable: bool = True,
    ) -> tuple[bool, str]:
        """Publish a message to the specified RabbitMQ queue.

        Parameters
        ----------
        message : RabbitMQPayload
            The message payload to be sent.
        queue_name : str
            The name of the target RabbitMQ queue.
        routing_key : str, optional
            The routing key for the message, by default None.
        request_id : str, optional
            The request ID for the message for tracking, by default None.
        durable : bool, optional
            Whether the queue should be durable, by default True.

        Returns
        -------
        tuple[bool, str]
            Tuple indicating success status and task ID.
        """
        routing_key = routing_key or queue_name
        delivery_mode = aio_pika.DeliveryMode.PERSISTENT if durable else aio_pika.DeliveryMode.NOT_PERSISTENT
        timestamp = datetime.now()
        task_id = str(uuid4())

        if self.channel is None:
            logger.error("[-] Cannot publish message: Channel is not established")
            return (False, "")

        try:
            # Create and publish the message
            body = json.dumps(message.to_dict()).encode()
            rabbitmq_message = aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=delivery_mode,
                correlation_id=request_id,
                timestamp=timestamp,
                headers={"task_id": task_id},
            )
            await self.channel.default_exchange.publish(rabbitmq_message, routing_key=routing_key)

            logger.info(f"[+] Published message to queue '{queue_name}': {message}")
            return (True, task_id)

        except aio_pika.exceptions.AMQPException as e:
            logger.error(f"[-] AMQP error while publishing to queue '{queue_name}': {e}")
            return (False, "")

        except (json.JSONDecodeError, TypeError, UnicodeEncodeError) as e:
            logger.error(f"[-] Serialization error while publishing to queue '{queue_name}': {e}")
            return (False, "")

        except Exception as e:
            logger.error(f"[-] Failed to publish message to queue '{queue_name}': {e}")
            return (False, "")

    async def abatch_publish(
        self,
        messages: list[RabbitMQPayload],
        queue_name: str,
        routing_key: str | None = None,
        request_id: str | None = None,
        durable: bool = True,
    ) -> tuple[int, list[str]]:
        """Publish a batch of messages to RabbitMQ.

        Parameters
        ----------
        messages : list[RabbitMQPayload]
            List of message payloads to be sent.
        queue_name : str
            The name of the target RabbitMQ queue.
        routing_key : str | None, optional
            The routing key for the message, by default None
        request_id : str | None, optional
            The request ID for the message for tracking, by default None
        durable : bool, optional
            Whether the queue should be durable, by default True

        Returns
        -------
        tuple[int, list[str]]
            Tuple containing the number of successfully sent messages and their task IDs.

        """
        messages_sent: int = 0
        task_ids: list[str] = []

        for idx, msg in enumerate(messages):
            try:
                (success, task_id) = await self.apublish(
                    message=msg,
                    queue_name=queue_name,
                    routing_key=routing_key,
                    request_id=request_id,
                    durable=durable,
                )
                if success:
                    messages_sent += 1
                    task_ids.append(task_id)

            except Exception as e:
                logger.error(f"Failed to publish message {idx} in batch: {e}")
                continue

        logger.info(f"Batch publish completed: {messages_sent}/{len(messages)} messages sent")
        return (messages_sent, task_ids)
