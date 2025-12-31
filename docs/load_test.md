# TaskFlow Load Testing Guide

This document shows how to run realistic load tests against the TaskFlow API using Locust.

## Prerequisites

- API running (e.g., `docker-compose up -d`)
- Locust installed in your environment (`pip install locust`) or via `uv add --dev locust`
- Scripts present: `run-locust.sh`, `locustfile.py`

## Key Parameters

- **HOST**: Base URL (default `http://localhost:8000/api/v1`). Override per run.
- **users**: Concurrent virtual users (sessions).
- **spawn-rate**: Users started per second.
- **run-time**: Total test duration (e.g., `5m`, `30m`).
- **wait_time**: Think time between actions (set in `locustfile.py`, currently `between(1, 3)` for main users and `between(5, 15)` for burst users).
- **Task weights**: Control endpoint mix (health 3x, jobs normal 7x, bulk 2x, high priority 1x, burst user class 1x task).
- **Payload**: Messages include numeric `priority` (0–255) as expected by RabbitMQ/aio_pika.

## Quick Commands (using helper script)

Run from repo root. Set `HOST` if not using default.

```bash
# Interactive web UI (http://localhost:8089)
./run-locust.sh interactive

# Smoke test (light): 5 users, 2 spawn/sec, 30s
./run-locust.sh smoke

# Steady load: 200 users, 20 spawn/sec, 15 minutes
./run-locust.sh headless 200 20 15m

# Spike test: ramp fast to 1000 users, 200 spawn/sec, 20 minutes
./run-locust.sh headless 1000 200 20m

# Stress test (aggressive): 500 users, 50 spawn/sec, 10 minutes
./run-locust.sh stress

# Override host (example production/stage endpoint)
HOST=https://api.example.com/api/v1 ./run-locust.sh headless 300 30 20m
```

## Direct Locust Commands (without helper)

```bash
# Interactive UI
locust -f locustfile.py -H http://localhost:8000/api/v1

# Headless with parameters
locust -f locustfile.py -H http://localhost:8000/api/v1 \
  --users 300 \
  --spawn-rate 50 \
  --run-time 15m \
  --headless
```

## Suggested Scenarios

- **Smoke**: Verify endpoints and payloads
  - users: 5–20, spawn-rate: 5–10, run-time: 1–2m
- **Baseline steady state**: Match expected daily average
  - users: 100–300, spawn-rate: 20–50, run-time: 10–30m
- **Spike**: Sudden surges (marketing/cron bursts)
  - users: 500–1000, spawn-rate: 100–200, run-time: 10–20m
- **Soak**: Long stability
  - users: 100–200, spawn-rate: 10–30, run-time: 1–3h
- **Stress to breaking point**: Find limits
  - users: increase stepwise (e.g., 200 → 400 → 800 → 1200), keep spawn-rate proportional (50–200)

## How to Tune Toward Real RPS

- Increase `users` to raise concurrency; increase `spawn-rate` to reach it faster.
- Increase `wait_time` upper/lower bounds to **reduce** RPS per user; decrease to **raise** RPS.
- If you need precise RPS, switch to arrival-rate executors (Locust `--step-load` or `ramping-arrival-rate` via a custom scenario). Ask and we can add that variant.

## What to Watch

- API metrics: p95 latency, error rate, 429/5xx counts.
- RabbitMQ: queue length, consumer lag, connection/channel limits.
- DB/Cache: CPU, connections, slow queries, cache hit rate.
- Host resources: CPU, memory, network, file descriptors.

## Exit Criteria (example)

- p95 < 500ms at target load; error rate < 1%.
- No growing queue/backlog during steady-state.
- Graceful behavior under spike (no crash/oom) and recovers once load drops.

## Troubleshooting

- 404/422: ensure HOST includes `/api/v1` and payload schema matches (messages must be wrapped under `payload`).
- High failure rate: check rate limits, DB/RabbitMQ saturation, or upstream errors.
- Locust saturates load-generator CPU: lower users per worker or run distributed Locust workers.
