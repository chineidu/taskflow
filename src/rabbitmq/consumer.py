import asyncio
import json
from typing import TYPE_CHECKING, Any, Callable

from src import create_logger
from src.config import app_settings
from src.rabbitmq.base import BaseRabbitMQ
from src.schemas.rabbitmq.payload import RabbitMQPayload

if TYPE_CHECKING:
    from src.config.config import AppConfig

logger = create_logger("consumer")
RABBITMQ_URL: str = app_settings.rabbitmq_url

# Takes a [deserialized message dict] and returns [Any]
type CONSUMER_CALLBACK_FN = Callable[[RabbitMQPayload], Any]


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
                    try:
                        async with message.process(
                            requeue=False,
                            reject_on_redelivered=False,
                            ignore_processed=False,
                        ):
                            # Deserialize message
                            try:
                                message_body = message.body.decode()
                                message_dict = json.loads(message_body)

                            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                                logger.error(
                                    f"Failed to decode message with "
                                    f"correlation_id={message.correlation_id}: {e}"
                                )
                                # Skip processing this message
                                continue

                            # Call the provided callback
                            if asyncio.iscoroutinefunction(callback):
                                await callback(message_dict)
                            else:
                                callback(message_dict)

                    except asyncio.CancelledError:
                        logger.info("Consumer task was cancelled.")
                        break

                    except Exception as e:
                        logger.error(
                            f"Error processing message with correlation_id={message.correlation_id}: {e}"
                        )
        except Exception as e:
            logger.error(f"Unexpected error consuming from queue '{queue_name}': {e}")


async def example_consumer_callback(message: RabbitMQPayload) -> None:
    """Example consumer callback function.

    Parameters
    ----------
    message : RabbitMQPayload
        The deserialized message payload.
    """
    # Simulate processing
    await asyncio.sleep(1)
    logger.info(f"Processing message: {message}")


async def main() -> None:
    """Example usage of the production-ready consumer."""
    from src.config import app_config

    consumer = RabbitMQConsumer(app_config)

    try:
        # Consume with async callback using context manager
        async with consumer.aconnection_context():
            await consumer.consume(
                queue_name="test_queue",
                callback=example_consumer_callback,
                durable=True,
            )

    except Exception as e:
        logger.error(f"[-] Error in main: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
