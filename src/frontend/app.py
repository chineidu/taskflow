import asyncio
from datetime import datetime
from typing import Any, Coroutine

import streamlit as st

from src.config import app_settings
from src.utilities import HTTPXClient

# -------------------------
# Config (MUST be first)
# -------------------------
st.set_page_config(
    page_title="TaskFlow Dashboard",
    page_icon="📊",
    layout="wide",
)

SERVER_HOST: str = app_settings.HOST
SERVER_PORT: int = app_settings.PORT

BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/api/v1"
BACKEND_URL = "/metrics"
REFRESH_INTERVAL_SECONDS = 10


# -------------------------
# Async Utilities
# -------------------------
def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async coroutine in sync context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        return loop.run_until_complete(coro)


# -------------------------
# Data Layer
# -------------------------
@st.cache_data(ttl=5)
def get_metrics() -> dict[str, Any]:
    """Fetch metrics from the backend API."""

    async def _fetch() -> Any:
        async with HTTPXClient(base_url=BASE_URL) as client:
            response = await client.get(BACKEND_URL)
            return response["data"]

    return run_async(_fetch())


# -------------------------
# UI Helpers
# -------------------------
def metric_card(
    label: str, value: Any, delta: str | None = None, help_text: str | None = None, status: str | None = None
) -> None:
    """Render an enhanced metric card with status indicators."""
    status_icons = {"success": "✓", "warning": "⚠", "error": "✗", "info": "ℹ"}

    if status:
        label = f"{status_icons.get(status, '')} {label}"

    st.metric(label=label, value=value, delta=delta, help=help_text)


def get_system_status(health: dict[str, Any], dashboard: dict[str, Any]) -> tuple[str, str]:
    """Determine overall system status."""
    if dashboard["failed"] > dashboard["totalTasks"] * 0.1:  # >10% failure rate
        return "🔴", "Degraded"
    if health["workersOnline"] == 0:
        return "🔴", "Offline"
    if dashboard["pending"] > 100:
        return "🟡", "High Load"
    return "🟢", "Healthy"


# -------------------------
# Dashboard
# -------------------------
def render_dashboard(metrics: dict[str, Any]) -> None:
    """Render the main dashboard UI."""
    dashboard = metrics["dashboardMetrics"]
    health = metrics["systemHealth"]

    # -------------------------
    # Header
    # -------------------------
    col1, col2, col3 = st.columns([3, 1, 1])

    with col1:
        status_icon, status_text = get_system_status(health, dashboard)
        st.title(f"TaskFlow Dashboard {status_icon}")
        st.caption(f"System Status: **{status_text}** • Last updated: {datetime.now().strftime('%H:%M:%S')}")

    with col2:
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    with col3:
        auto_refresh = st.checkbox("Auto-refresh", value=st.session_state.get("auto_refresh_enabled", False))
        st.session_state.auto_refresh_enabled = auto_refresh
        if auto_refresh:
            st.caption(f"Updates every {REFRESH_INTERVAL_SECONDS}s")

    # Alert banner
    if dashboard["failed"] > 0:
        failure_rate = (
            (dashboard["failed"] / dashboard["totalTasks"] * 100) if dashboard["totalTasks"] > 0 else 0
        )
        st.error(
            f"⚠️ **{dashboard['failed']} tasks failed** ({failure_rate:.1f}% "
            "failure rate) — Check logs for details"
        )

    if health["workersOnline"] == 0:
        st.error("🔴 **No workers online** — Task processing is halted")

    st.divider()

    # -------------------------
    # Performance Metrics
    # -------------------------
    st.subheader("⚡ Performance")

    perf_col1, perf_col2, perf_col3, perf_col4 = st.columns(4)

    with perf_col1:
        metric_card(
            "Avg Processing Time",
            f"{metrics['avgProcessingTimeSeconds']:.2f}s",
            help_text="Average execution time per task",
        )

    with perf_col2:
        worker_status = "success" if health["workersOnline"] > 0 else "error"
        metric_card(
            "Workers Online",
            health["workersOnline"],
            help_text="Active worker processes",
            status=worker_status,
        )

    with perf_col3:
        metric_card("Queue Depth", health["messagesReady"], help_text="Tasks waiting in queue")

    with perf_col4:
        completion_rate = (
            (dashboard["completed"] / dashboard["totalTasks"] * 100) if dashboard["totalTasks"] > 0 else 0
        )
        metric_card("Completion Rate", f"{completion_rate:.1f}%", help_text="Successfully completed tasks")

    st.divider()

    # -------------------------
    # Task Overview
    # -------------------------
    st.subheader("📋 Task Status Overview")

    task_col1, task_col2, task_col3, task_col4, task_col5 = st.columns(5)

    metrics_config = [
        ("Total Tasks", dashboard["totalTasks"], "info", "All tasks in the system"),
        (
            "Pending",
            dashboard["pending"],
            "warning" if dashboard["pending"] > 50 else "info",
            "Waiting for processing",
        ),
        ("In Progress", dashboard["inProgress"], "info", "Currently being processed"),
        ("Completed", dashboard["completed"], "success", "Successfully finished"),
        (
            "Failed",
            dashboard["failed"],
            "error" if dashboard["failed"] > 0 else "info",
            "Processing failures",
        ),
    ]

    for col, (label, value, status, help_text) in zip(
        [task_col1, task_col2, task_col3, task_col4, task_col5], metrics_config
    ):
        with col:
            metric_card(label, value, help_text=help_text, status=status)

    st.divider()

    # -------------------------
    # Visualizations
    # -------------------------
    st.subheader("📊 Analytics")

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.markdown("**Task Distribution**")

        # Calculate percentages
        total = dashboard["totalTasks"] if dashboard["totalTasks"] > 0 else 1
        task_data = {
            "Status": ["Completed", "In Progress", "Pending", "Failed"],
            "Count": [
                dashboard["completed"],
                dashboard["inProgress"],
                dashboard["pending"],
                dashboard["failed"],
            ],
            "Percentage": [
                f"{(dashboard['completed'] / total * 100):.1f}%",
                f"{(dashboard['inProgress'] / total * 100):.1f}%",
                f"{(dashboard['pending'] / total * 100):.1f}%",
                f"{(dashboard['failed'] / total * 100):.1f}%",
            ],
        }

        st.bar_chart(data=dict(zip(task_data["Status"], task_data["Count"])), horizontal=True, height=250)

        # Show percentages below chart
        st.caption(" • ".join([f"{s}: {p}" for s, p in zip(task_data["Status"], task_data["Percentage"])]))

    with chart_col2:
        st.markdown("**System Resources**")

        resource_data = {
            "Workers Online": health["workersOnline"],
            "Messages Ready": health["messagesReady"],
            "DLQ Tasks": dashboard["dlqCounts"],
        }

        st.bar_chart(data=resource_data, height=250)

    st.divider()

    # -------------------------
    # Detailed Stats
    # -------------------------
    with st.expander("🔍 Detailed Statistics", expanded=False):
        detail_col1, detail_col2 = st.columns(2)

        with detail_col1:
            st.markdown("**Queue Health**")
            st.metric("Messages Ready", health["messagesReady"])
            st.metric("Dead Letter Queue", dashboard["dlqCounts"], help="Tasks that failed repeatedly")

        with detail_col2:
            st.markdown("**Processing Stats**")
            if dashboard["totalTasks"] > 0:
                success_rate = (dashboard["completed"] / dashboard["totalTasks"]) * 100
                failure_rate = (dashboard["failed"] / dashboard["totalTasks"]) * 100
                st.metric("Success Rate", f"{success_rate:.2f}%")
                st.metric("Failure Rate", f"{failure_rate:.2f}%")

    # -------------------------
    # Footer
    # -------------------------
    st.caption("---")
    st.caption(
        "💡 Dashboard refreshes every 5 seconds when cache expires "
        "• Enable auto-refresh for continuous updates"
    )


# -------------------------
# Entrypoint
# -------------------------
try:
    render_dashboard(get_metrics())

    # Auto-refresh logic (non-blocking)
    if "last_refresh" not in st.session_state:
        st.session_state.last_refresh = datetime.now()

    # Check if auto-refresh checkbox is enabled
    # This uses Streamlit's fragment API for efficient rerunning
    if st.session_state.get("auto_refresh_enabled", False):
        import time

        time.sleep(REFRESH_INTERVAL_SECONDS)
        st.rerun()

except Exception as exc:
    st.error("❌ Failed to fetch metrics from backend API")

    with st.expander("Error Details", expanded=True):
        st.exception(exc)

    st.info(
        "🔧 **Troubleshooting:**\n- Ensure the backend API is running on `http://0.0.0.0:8000`"
        "\n- Check network connectivity\n- Verify API endpoint: `/api/v1/metrics`"
    )

    if st.button("🔄 Retry Connection"):
        st.cache_data.clear()
        st.rerun()
