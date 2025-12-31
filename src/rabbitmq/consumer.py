import asyncio
import json
import signal
import sys
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
                                continue

                            # Call the provided callback
                            if asyncio.iscoroutinefunction(callback):
                                await callback(message_dict)
                            else:
                                callback(message_dict)

                    except asyncio.CancelledError:
                        logger.info("[+] Message processing cancelled")
                        raise

                    except Exception as e:
                        logger.error(f"Error processing message: {e}")

            logger.info("[+] Exiting consume loop")

        except asyncio.CancelledError:
            logger.info("[+] Consumer task cancelled")
            raise
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
                    queue_name="test_queue",
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
        logger.error(f"[-] Error in main: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
        logger.info("[+] Exiting gracefully")

    except KeyboardInterrupt:
        logger.info("[+] Received KeyboardInterrupt, exiting...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"[-] Fatal error running consumer: {e}")
        sys.exit(1)
