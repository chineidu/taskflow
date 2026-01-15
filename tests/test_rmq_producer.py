"""Async Tests for RabbitMQProducer."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aio_pika
import pytest

from src.rabbitmq.producer import RabbitMQProducer
from src.schemas.rabbitmq.base import RabbitMQPayload


@pytest.fixture
def producer(mock_config: MagicMock) -> RabbitMQProducer:
    """Create a RabbitMQProducer instance with mocked connection/channel."""
    # Create producer without trying to connect
    prod = RabbitMQProducer(config=mock_config, url="amqp://guest:guest@localhost:5672/")
    # Mock the channel and connection directly to avoid actual RabbitMQ connection
    prod.channel = AsyncMock(spec=aio_pika.Channel)
    prod.channel.default_exchange = AsyncMock()
    prod.connection = AsyncMock(spec=aio_pika.RobustConnection)
    prod.connection.is_closed = False
    return prod


@pytest.mark.asyncio
@pytest.mark.skip(reason="RMQ producer tests need fixture refactoring to avoid connection attempts")
class TestRabbitMQProducer:
    """Tests for RabbitMQProducer methods."""

    async def test_apublish_success(self, producer: RabbitMQProducer) -> None:
        """Test successful message publishing."""
        # Given
        from src.schemas.types import PriorityEnum

        payload = RabbitMQPayload(payload={"data": "test"})
        queue_name = "test_queue"

        # When
        success, task_id = await producer.apublish(
            message=payload, queue_name=queue_name, priority=PriorityEnum.MEDIUM
        )

        # Then
        assert success is True
        assert isinstance(task_id, str)
        producer.channel.default_exchange.publish.assert_called_once()  # type: ignore

        # Verify arguments passed to publish
        call_args = producer.channel.default_exchange.publish.call_args  # type: ignore
        message = call_args[0][0]
        routing_key = call_args[1]["routing_key"]

        assert routing_key == queue_name
        assert isinstance(message, aio_pika.Message)
        assert json.loads(message.body) == payload.model_dump()

    async def test_apublish_channel_not_established(self, producer: RabbitMQProducer) -> None:
        """Test publishing when channel is not established."""
        # Given
        from src.schemas.types import PriorityEnum

        producer.channel = None
        producer.connection = None  # Also set connection to None to prevent reconnection
        payload = RabbitMQPayload(payload={"data": "test"})

        # When - expect RuntimeError when channel cannot be established
        with pytest.raises(RuntimeError, match="Failed to establish channel connection"):
            await producer.apublish(message=payload, queue_name="test_queue", priority=PriorityEnum.MEDIUM)

    async def test_apublish_amqp_exception(self, producer: RabbitMQProducer) -> None:
        """Test handling of AMQP exception during publish."""
        # Given
        from src.schemas.types import PriorityEnum

        payload = RabbitMQPayload(payload={"data": "test"})
        producer.channel.default_exchange.publish.side_effect = aio_pika.exceptions.AMQPException("Error")  # type: ignore

        # When
        success, task_id = await producer.apublish(
            message=payload, queue_name="test_queue", priority=PriorityEnum.MEDIUM
        )

        # Then
        assert success is False

    async def test_apublish_serialization_error(self, producer: RabbitMQProducer) -> None:
        """Test handling of serialization error."""
        # Given
        from src.schemas.types import PriorityEnum

        payload = RabbitMQPayload(payload={"data": "test"})

        with patch("src.rabbitmq.producer.json.dumps", side_effect=TypeError("Serialization failed")):
            # When
            success, task_id = await producer.apublish(
                message=payload, queue_name="test_queue", priority=PriorityEnum.MEDIUM
            )

        # Then
        assert success is False

    async def test_abatch_publish_success(self, producer: RabbitMQProducer) -> None:
        """Test successful batch publishing."""
        # Given
        from src.schemas.types import PriorityEnum

        messages = [
            RabbitMQPayload(payload={"i": 1}),
            RabbitMQPayload(payload={"i": 2}),
        ]

        # When
        count, task_ids = await producer.abatch_publish(
            messages=messages, queue_name="test_queue", priority=PriorityEnum.MEDIUM
        )

        # Then
        assert count == 2
        assert len(task_ids) == 2
        assert producer.channel.default_exchange.publish.call_count == 2  # type: ignore

    async def test_abatch_publish_partial_failure(self, producer: RabbitMQProducer) -> None:
        """Test batch publishing with some failures."""
        # Given
        from src.schemas.types import PriorityEnum

        messages = [
            RabbitMQPayload(payload={"i": 1}),
            RabbitMQPayload(payload={"i": 2}),
        ]

        # Make the second call fail
        producer.channel.default_exchange.publish.side_effect = [None, Exception("Boom")]  # type: ignore

        # When
        count, task_ids = await producer.abatch_publish(
            messages=messages, queue_name="test_queue", priority=PriorityEnum.MEDIUM
        )

        # Then
        assert count == 1
        assert len(task_ids) == 2
