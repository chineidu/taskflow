"""Async Tests for RabbitMQConsumer."""

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import aio_pika
import pytest

from src.rabbitmq.consumer import RabbitMQConsumer
from src.schemas.types import TaskStatusEnum


@pytest.fixture
def consumer(mock_config: MagicMock) -> RabbitMQConsumer:
    """Create a RabbitMQConsumer instance with mocked connection/channel.

    Parameters
    ----------
    mock_config : MagicMock
        Mocked configuration.

    Returns
    -------
    RabbitMQConsumer
        Consumer instance with mocked internals.
    """
    with patch("src.rabbitmq.base.aio_pika.connect_robust", new_callable=AsyncMock):
        cons = RabbitMQConsumer(config=mock_config, url="amqp://guest:guest@localhost:5672/")
        cons.channel = AsyncMock(spec=aio_pika.Channel)
        cons.connection = AsyncMock(spec=aio_pika.RobustConnection)
        # Simulate already connected state to prevent aconnect from trying to connect
        cons.connection.is_closed = False
        return cons


@pytest.mark.asyncio
class TestRabbitMQConsumer:
    """Tests for RabbitMQConsumer methods."""

    async def test_consume_success(self, consumer: RabbitMQConsumer) -> None:
        """Test successful message consumption and processing.

        Parameters
        ----------
        consumer : RabbitMQConsumer
            The consumer instance to test.
        """
        # Mocks
        mock_queue = AsyncMock()
        # queue.iterator() is a sync method returning an async context manager
        mock_queue.iterator = MagicMock()
        consumer.aensure_queue = AsyncMock(return_value=mock_queue)
        consumer.aensure_dlq = AsyncMock()

        # Mock message
        mock_message = AsyncMock(spec=aio_pika.IncomingMessage)
        mock_message.body = json.dumps({"data": "test"}).encode()
        mock_message.headers = {"task_id": "task-123"}
        mock_message.correlation_id = "corr-123"
        mock_message.timestamp = 1234567890
        mock_message.process.return_value.__aenter__.return_value = None

        # Mock queue iterator
        mock_queue_iter_ctx = AsyncMock()
        mock_queue_iter = AsyncMock()

        mock_queue.iterator.return_value = mock_queue_iter_ctx
        mock_queue_iter_ctx.__aenter__.return_value = mock_queue_iter

        async def message_generator() -> AsyncGenerator[AsyncMock, None]:
            yield mock_message
            # Trigger shutdown to exit loop after one message
            consumer._shutdown_event.set()

        mock_queue_iter.__aiter__.side_effect = message_generator

        # Mock dependencies
        with (
            patch("src.rabbitmq.consumer.aget_db_session", new_callable=MagicMock),
            patch("src.rabbitmq.consumer.TaskRepository") as MockRepo,
            patch("src.rabbitmq.consumer.S3StorageService") as MockS3,
            patch("src.rabbitmq.consumer.add_file_handler"),
            patch("src.rabbitmq.consumer.tempfile.NamedTemporaryFile") as mock_tmp,
            patch("src.rabbitmq.consumer.Path") as MockPath,
        ):
            # Setup Repo Mock
            mock_repo_instance = MockRepo.return_value
            mock_repo_instance.aupdate_task_status = AsyncMock()
            mock_repo_instance.aupdate_log_info = AsyncMock()

            # Setup S3 Mock
            mock_s3_instance = MockS3.return_value
            mock_s3_instance.aupload_file_to_s3 = AsyncMock()
            mock_s3_instance.get_object_name.return_value = "logs/task-123.log"
            mock_s3_instance.get_s3_object_url.return_value = "s3://bucket/logs/task-123.log"

            # Setup Temp File Mock
            mock_file = MagicMock()
            mock_file.name = "/tmp/test.log"
            mock_tmp.return_value.__enter__.return_value = mock_file

            # Mock Path stat size
            MockPath.return_value.stat.return_value.st_size = 100
            MockPath.return_value.exists.return_value = True

            # Callback
            callback = AsyncMock()

            # Run consume
            await consumer.consume(queue_name="test_queue", callback=callback)

            # Assertions
            # Verify DLQ is ensured
            consumer.aensure_dlq.assert_called_once()

            # Verify queue is ensured with DLX arguments
            consumer.aensure_queue.assert_called_once()
            call_args = consumer.aensure_queue.call_args
            assert call_args.kwargs["queue_name"] == "test_queue"
            assert call_args.kwargs["durable"] is True
            assert "x-dead-letter-exchange" in call_args.kwargs["arguments"]

            callback.assert_called_once_with({"data": "test"})

            # Verify DB updates
            assert mock_repo_instance.aupdate_task_status.call_count == 2
            mock_repo_instance.aupdate_task_status.assert_any_call(
                "task-123", status=TaskStatusEnum.IN_PROGRESS
            )
            mock_repo_instance.aupdate_task_status.assert_any_call(
                "task-123", status=TaskStatusEnum.COMPLETED
            )

            # Verify S3 upload
            mock_s3_instance.aupload_file_to_s3.assert_called_once()
            mock_repo_instance.aupdate_log_info.assert_called_once()

    async def test_consume_callback_failure(self, consumer: RabbitMQConsumer) -> None:
        """Test message consumption when callback fails.

        Parameters
        ----------
        consumer : RabbitMQConsumer
            The consumer instance to test.
        """
        # Mocks
        mock_queue = AsyncMock()
        # queue.iterator() is a sync method returning an async context manager
        mock_queue.iterator = MagicMock()
        consumer.aensure_queue = AsyncMock(return_value=mock_queue)
        consumer.aensure_dlq = AsyncMock()

        # Mock message
        mock_message = AsyncMock(spec=aio_pika.IncomingMessage)
        mock_message.body = json.dumps({"data": "test"}).encode()
        mock_message.headers = {"task_id": "task-123"}
        mock_message.correlation_id = "corr-123"
        mock_message.timestamp = 1234567890
        mock_message.process.return_value.__aenter__.return_value = None

        # Mock queue iterator
        mock_queue_iter_ctx = AsyncMock()
        mock_queue_iter = AsyncMock()

        mock_queue.iterator.return_value = mock_queue_iter_ctx
        mock_queue_iter_ctx.__aenter__.return_value = mock_queue_iter

        async def message_generator() -> AsyncGenerator[AsyncMock, None]:
            yield mock_message
            consumer._shutdown_event.set()

        mock_queue_iter.__aiter__.side_effect = message_generator

        # Mock dependencies
        with (
            patch("src.rabbitmq.consumer.aget_db_session", new_callable=MagicMock),
            patch("src.rabbitmq.consumer.TaskRepository") as MockRepo,
            patch("src.rabbitmq.consumer.S3StorageService") as MockS3,
            patch("src.rabbitmq.consumer.add_file_handler"),
            patch("src.rabbitmq.consumer.tempfile.NamedTemporaryFile") as mock_tmp,
            patch("src.rabbitmq.consumer.Path") as MockPath,
        ):
            # Setup Repo Mock
            mock_repo_instance = MockRepo.return_value
            mock_repo_instance.aupdate_task_status = AsyncMock()
            mock_repo_instance.aupdate_log_info = AsyncMock()

            # Setup S3 Mock
            mock_s3_instance = MockS3.return_value
            mock_s3_instance.aupload_file_to_s3 = AsyncMock()

            # Setup Temp File Mock
            mock_file = MagicMock()
            mock_file.name = "/tmp/test.log"
            mock_tmp.return_value.__enter__.return_value = mock_file

            # Mock Path stat size
            MockPath.return_value.stat.return_value.st_size = 100
            MockPath.return_value.exists.return_value = True

            # Callback that fails
            callback = AsyncMock(side_effect=Exception("Processing failed"))

            # Run consume
            await consumer.consume(queue_name="test_queue", callback=callback)

            # Assertions
            callback.assert_called_once()
            consumer.aensure_dlq.assert_called_once()

            # Verify DB updates - should be FAILED
            mock_repo_instance.aupdate_task_status.assert_any_call(
                "task-123", status=TaskStatusEnum.IN_PROGRESS
            )
            mock_repo_instance.aupdate_task_status.assert_any_call(
                "task-123", status=TaskStatusEnum.FAILED, error="Processing failed"
            )

            # Verify S3 upload still happens
            mock_s3_instance.aupload_file_to_s3.assert_called_once()

    async def test_consume_json_decode_error(self, consumer: RabbitMQConsumer) -> None:
        """Test handling of malformed JSON message.

        Parameters
        ----------
        consumer : RabbitMQConsumer
            The consumer instance to test.
        """
        # Mocks
        mock_queue = AsyncMock()
        # queue.iterator() is a sync method returning an async context manager
        mock_queue.iterator = MagicMock()
        consumer.aensure_queue = AsyncMock(return_value=mock_queue)
        consumer.aensure_dlq = AsyncMock()

        # Mock message with invalid JSON
        mock_message = AsyncMock(spec=aio_pika.IncomingMessage)
        mock_message.body = b"invalid json"
        mock_message.headers = {"task_id": "task-123"}
        mock_message.correlation_id = "corr-123"
        mock_message.process.return_value.__aenter__.return_value = None

        # Mock queue iterator
        mock_queue_iter_ctx = AsyncMock()
        mock_queue_iter = AsyncMock()

        mock_queue.iterator.return_value = mock_queue_iter_ctx
        mock_queue_iter_ctx.__aenter__.return_value = mock_queue_iter

        async def message_generator() -> AsyncGenerator[AsyncMock, None]:
            yield mock_message
            consumer._shutdown_event.set()

        mock_queue_iter.__aiter__.side_effect = message_generator

        # Callback
        callback = AsyncMock()

        # Run consume
        await consumer.consume(queue_name="test_queue", callback=callback)

        # Assertions
        # Callback should NOT be called
        callback.assert_not_called()
        consumer.aensure_dlq.assert_called_once()
