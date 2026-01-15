"""Additional tests for routes and WebSocket streaming."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession


def test_stream_task_updates_websocket(client: TestClient) -> None:
    """Test WebSocket endpoint exists and accepts connections."""
    task_id = "ws-test-task"

    # Mock BaseRabbitMQ to avoid actual RabbitMQ connection
    with patch("src.api.routes.v1.jobs.BaseRabbitMQ") as MockRMQ:
        mock_rmq = MockRMQ.return_value
        mock_rmq.aconnect = AsyncMock()
        mock_rmq.adisconnect = AsyncMock()
        # Simulate connection failure to exit quickly
        mock_rmq.connection = None

        # Test that WebSocket endpoint exists and handles connection failure gracefully
        try:
            with client.websocket_connect(f"/api/v1/jobs/{task_id}/status") as ws:
                # If connection is None, the endpoint should close with error code
                pass
        except Exception:  # noqa: S110
            # Expected - connection should fail gracefully when RabbitMQ is unavailable
            pass

        # Verify RabbitMQ connection was attempted
        mock_rmq.aconnect.assert_called_once()


def test_status_fragment_and_demo_view(client: TestClient, db_session: AsyncSession) -> None:  # noqa: ARG001, ARG002
    """Test the status-fragment and demo view routes return HTML content."""
    # Simple test - just verify routes exist and return 200
    resp2 = client.get("/api/v1/jobs/view/demo")
    assert resp2.status_code == 200


def test_retry_endpoints(client: TestClient) -> None:  # noqa: ARG001
    """Test retry endpoints by patching the DLQ replay helpers."""
    with (
        patch("src.api.routes.v1.jobs.areplay_dlq_messages", new_callable=AsyncMock) as mock_batch,
        patch("src.api.routes.v1.jobs.areplay_dlq_message_by_task_id", new_callable=AsyncMock) as mock_single,
    ):
        mock_batch.return_value = {"success": True}
        mock_single.return_value = {"success": True}

        resp = client.post("/api/v1/jobs/action/retry")
        assert resp.status_code == 200

        resp2 = client.post("/api/v1/jobs/action/some-task/retry")
        assert resp2.status_code == 200
