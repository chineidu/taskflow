"""
Production-grade load testing for TaskFlow API using Locust.
Run with: locust -f locustfile.py -H http://localhost:8000/api/v1
"""

import random
import time
from datetime import datetime

from locust import between, events, task
from locust.contrib.fasthttp import FastHttpUser


class APIUser(FastHttpUser):
    """Simulates real-world API usage patterns."""

    wait_time = between(1, 3)  # Wait 1-3 seconds between requests

    def on_start(self):
        """Called when a user starts."""
        self.user_id = random.randint(1000, 9999)

    @task(3)
    def health_check(self):
        """Health check endpoint - weighted 3x."""
        with self.client.get(
            "/health", catch_response=True, name="GET /health"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got status {response.status_code}")

    @task(7)
    def submit_job_normal(self):
        """Submit normal job - weighted 7x (more common)."""
        payload = {
            "data": self._generate_messages(count=5),
            "queue_name": "default_queue",
        }

        with self.client.post(
            "/jobs", json=payload, catch_response=True, name="POST /jobs (normal)"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got status {response.status_code}: {response.text}")

    @task(2)
    def submit_job_bulk(self):
        """Submit bulk job - weighted 2x (less common)."""
        payload = {
            "data": self._generate_messages(count=20),
            "queue_name": "bulk_queue",
        }

        with self.client.post(
            "/jobs", json=payload, catch_response=True, name="POST /jobs (bulk)"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got status {response.status_code}: {response.text}")

    @task(1)
    def submit_job_high_priority(self):
        """Submit high-priority job - weighted 1x (rare)."""
        payload = {
            "data": [
                {
                    "payload": {
                        "id": f"urgent-{int(time.time())}-{random.randint(0, 999)}",
                        "content": "URGENT: Critical processing needed",
                        "timestamp": datetime.now().isoformat(),
                        "priority": 200,  # high priority within 0-255 range
                    }
                }
            ],
            "queue_name": "priority_queue",
        }

        with self.client.post(
            "/jobs",
            json=payload,
            catch_response=True,
            name="POST /jobs (high priority)",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got status {response.status_code}: {response.text}")

    def _generate_messages(self, count: int) -> list:
        """Generate test messages wrapped in payload objects to satisfy schema."""
        return [
            {
                "payload": {
                    "id": f"msg-{int(time.time())}-{i}",
                    "content": f"Test message {i} - {random.choice(['processing', 'analytics', 'notification'])}",
                    "timestamp": datetime.now().isoformat(),
                    "priority": random.choice(
                        [0, 0, 0, 200]
                    ),  # 75% low/normal, 25% higher priority
                }
            }
            for i in range(count)
        ]


class BurstAPIUser(FastHttpUser):
    """Simulates burst traffic patterns (less frequent but important)."""

    wait_time = between(5, 15)  # Less frequent users

    @task
    def burst_submission(self):
        """Simulate burst of submissions."""
        payload = {
            "data": self._generate_messages(count=50),
            "queue_name": "burst_queue",
        }

        with self.client.post(
            "/jobs", json=payload, catch_response=True, name="POST /jobs (burst)"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got status {response.status_code}: {response.text}")

    def _generate_messages(self, count: int) -> list:
        """Generate test messages wrapped in payload objects to satisfy schema."""
        return [
            {
                "payload": {
                    "id": f"burst-{int(time.time())}-{i}",
                    "content": f"Bulk message {i}",
                    "timestamp": datetime.now().isoformat(),
                    "priority": 0,
                }
            }
            for i in range(count)
        ]


# Event handlers for custom reporting
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts."""
    print("\n" + "=" * 70)
    print("🚀 Load Test Starting")
    print(f"Target: {environment.host}")
    print("=" * 70 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops - generate summary."""
    print("\n" + "=" * 70)
    print("📊 Load Test Summary")
    print("=" * 70)

    stats = environment.stats
    print(f"\nTotal Requests: {stats.total.num_requests}")
    print(f"Failed Requests: {stats.total.num_failures}")
    print(f"Failure Rate: {stats.total.failure_rate:.2%}")
    print(f"Average Response Time: {stats.total.avg_response_time:.0f}ms")
    print(f"95th Percentile: {stats.total.get_response_time_percentile(0.95):.0f}ms")
    print(f"99th Percentile: {stats.total.get_response_time_percentile(0.99):.0f}ms")
    print(f"Min Response Time: {stats.total.min_response_time:.0f}ms")
    print(f"Max Response Time: {stats.total.max_response_time:.0f}ms")

    print("\n📈 Response by Endpoint:")
    for key, stat in stats.entries.items():
        method, path, _ = key
        print(f"  {method} {path}")
        print(
            f"    Requests: {stat.num_requests} | Avg: {stat.avg_response_time:.0f}ms | "
            f"95th%: {stat.get_response_time_percentile(0.95):.0f}ms | "
            f"Max: {stat.max_response_time:.0f}ms"
        )

    print("\n" + "=" * 70 + "\n")
