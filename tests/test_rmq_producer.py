"""Async Tests for RabbitMQProducer."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aio_pika
import pytest

from src.rabbitmq.producer import RabbitMQProducer
from src.schemas.rabbitmq.payload import RabbitMQPayload


@pytest.fixture
def producer(mock_config: MagicMock) -> RabbitMQProducer:
    """Create a RabbitMQProducer instance with mocked connection/channel."""
    with patch("src.rabbitmq.base.aio_pika.connect_robust", new_callable=AsyncMock):
        prod = RabbitMQProducer(config=mock_config, url="amqp://guest:guest@localhost:5672/")
        # Mock the channel and connection directly since we might not call aconnect in unit tests
        # or we can simulate a connected state.
        prod.channel = AsyncMock(spec=aio_pika.Channel)
        prod.channel.default_exchange = AsyncMock()
        prod.connection = AsyncMock(spec=aio_pika.RobustConnection)
        return prod


@pytest.mark.asyncio
class TestRabbitMQProducer:
    """Tests for RabbitMQProducer methods."""

    async def test_apublish_success(self, producer: RabbitMQProducer) -> None:
        """Test successful message publishing."""
        # Given
        payload = RabbitMQPayload(payload={"data": "test"})
        queue_name = "test_queue"

        # When
        success, task_id = await producer.apublish(message=payload, queue_name=queue_name)

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
        producer.channel = None
        payload = RabbitMQPayload(payload={"data": "test"})

        # When
        success, task_id = await producer.apublish(message=payload, queue_name="test_queue")

        # Then
        assert success is False
        assert isinstance(task_id, str)

    async def test_apublish_amqp_exception(self, producer: RabbitMQProducer) -> None:
        """Test handling of AMQP exception during publish."""
        # Given
        payload = RabbitMQPayload(payload={"data": "test"})
        producer.channel.default_exchange.publish.side_effect = aio_pika.exceptions.AMQPException("Error")  # type: ignore

        # When
        success, task_id = await producer.apublish(message=payload, queue_name="test_queue")

        # Then
        assert success is False

    async def test_apublish_serialization_error(self, producer: RabbitMQProducer) -> None:
        """Test handling of serialization error."""
        # Given
        payload = RabbitMQPayload(payload={"data": "test"})

        with patch("src.rabbitmq.producer.json.dumps", side_effect=TypeError("Serialization failed")):
            # When
            success, task_id = await producer.apublish(message=payload, queue_name="test_queue")

        # Then
        assert success is False

    async def test_abatch_publish_success(self, producer: RabbitMQProducer) -> None:
        """Test successful batch publishing."""
        # Given
        messages = [
            RabbitMQPayload(payload={"i": 1}),
            RabbitMQPayload(payload={"i": 2}),
        ]

        # When
        count, task_ids = await producer.abatch_publish(messages=messages, queue_name="test_queue")

        # Then
        assert count == 2
        assert len(task_ids) == 2
        assert producer.channel.default_exchange.publish.call_count == 2  # type: ignore

    async def test_abatch_publish_partial_failure(self, producer: RabbitMQProducer) -> None:
        """Test batch publishing with some failures."""
        # Given
        messages = [
            RabbitMQPayload(payload={"i": 1}),
            RabbitMQPayload(payload={"i": 2}),
        ]

        # Make the second call fail
        producer.channel.default_exchange.publish.side_effect = [None, Exception("Boom")]  # type: ignore

        # When
        count, task_ids = await producer.abatch_publish(messages=messages, queue_name="test_queue")

        # Then
        assert count == 1
        assert len(task_ids) == 2
