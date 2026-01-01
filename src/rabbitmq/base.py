import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator

import aio_pika

from src import create_logger
from src.config import app_settings

if TYPE_CHECKING:
    from src.config.config import AppConfig


logger = create_logger("rabbitmq.base")
RABBITMQ_URL: str = app_settings.rabbitmq_url


class BaseRabbitMQ:
    """RabbitMQ base class with connection management."""

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
        self.config = config
        self.url = url

        self.connection: aio_pika.RobustConnection | None = None
        self.channel: aio_pika.Channel | None = None
        self._lock = asyncio.Lock()

    async def aconnect(self) -> None:
        """Establish connection to RabbitMQ."""
        # Fast path: already connected
        if self.connection is not None and not self.connection.is_closed:
            return

        # If multiple coroutines try to connect simultaneously, ensure only one does the work
        async with self._lock:
            if self.connection is not None and not self.connection.is_closed:
                return

            # Connect with retries (if not already connected)
            for attempt in range(self.config.rabbitmq_config.max_retries):
                logger.info(
                    f"[+] Connecting to RabbitMQ (attempt {attempt + 1}/"
                    f"{self.config.rabbitmq_config.max_retries})"
                )
                try:
                    self.connection = await aio_pika.connect_robust(  # type: ignore
                        url=self.url,
                        timeout=self.config.rabbitmq_config.connection_timeout,
                        heartbeat=self.config.rabbitmq_config.heartbeat,
                    )
                    # Create a channel
                    if self.connection is not None:
                        self.channel = await self.connection.channel()  # type: ignore

                    # Set prefetch count
                    if self.channel is not None:
                        await self.channel.set_qos(
                            prefetch_count=self.config.rabbitmq_config.prefetch_count
                        )

                    if await self._is_connected():
                        logger.info("[+] Successfully connected to RabbitMQ")
                    return

                except aio_pika.exceptions.AMQPException as e:
                    if attempt < self.config.rabbitmq_config.max_retries - 1:
                        # Exponential backoff before retrying
                        delay = self.config.rabbitmq_config.retry_delay * (2**attempt)
                        logger.warning(
                            f"[-] Connection attempt {attempt + 1} failed: {e}. Retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        # All retries exhausted
                        logger.error(
                            "[-] Failed to connect to RabbitMQ after all retries"
                        )
                        raise

    async def adisconnect(self) -> None:
        """Close the connection to RabbitMQ."""
        if self.connection is not None and not self.connection.is_closed:
            try:
                await self.connection.close()
                logger.info("[+] Disconnected from RabbitMQ")

            except Exception as e:
                logger.error(f"[-] Error disconnecting: {e}")
            finally:
                self.connection = None
                self.channel = None

    async def _is_connected(self) -> bool:
        """Check if the producer is connected to RabbitMQ.

        Returns
        -------
        bool
            True if connected, False otherwise.
        """
        return self.connection is not None and not self.connection.is_closed

    async def aensure_queue(
        self,
        queue_name: str,
        durable: bool = True,
    ) -> aio_pika.abc.AbstractQueue:
        """Ensure queue exists with specified settings.

        Parameters
        ----------
        queue_name : str
            The name of the queue to ensure.
        durable : bool, optional
            Whether the queue should persist after broker restart, by default True.

        Returns
        -------
        aio_pika.abc.AbstractQueue
            The declared queue.
        """
        if self.channel is None:
            logger.debug("Channel not established, connecting...")
            await self.aconnect()

        if self.channel is None:
            raise RuntimeError("[-] Failed to establish channel connection")

        try:
            queue = await self.channel.declare_queue(queue_name, durable=durable)
            logger.debug(f"Queue '{queue_name}' ensured")
            return queue

        except aio_pika.exceptions.AMQPException as e:
            logger.error(f"Failed to declare queue '{queue_name}': {e}")
            raise

    @asynccontextmanager
    async def aconnection_context(self) -> AsyncGenerator[None, None]:
        """Async context manager for RabbitMQ connection.

        Yields
        ------
        AsyncGenerator[None, None]
            Yields control to the context block with an active connection.

        Usage
        -----
            producer = RabbitMQProducer(config, url)
            producer.ensure_queue("my_queue")
            async with producer.connection_context():
                await producer.publish(message, queue_name)
        """
        try:
            await self.aconnect()
            yield

        finally:
            await self.adisconnect()
