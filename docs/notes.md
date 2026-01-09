# Documentation of Thought Process

This document serves as an Architecture Decision Record (ADR) this project, detailing the key decisions made during the design and implementation.

Each section outlines a specific architectural choice, the rationale behind it, and any trade-offs considered.

## Table of Contents
<!-- TOC -->

- [Documentation of Thought Process](#documentation-of-thought-process)
  - [Table of Contents](#table-of-contents)
  - [Status During Task Processing](#status-during-task-processing)
    - [Explicit State Machine IN_PROGRESS](#explicit-state-machine-in_progress)
  - [Database Related Decisions](#database-related-decisions)
    - [Validation Strategy: The "Validation Tax"](#validation-strategy-the-validation-tax)
    - [Database Indexing Strategy](#database-indexing-strategy)
    - [Database Session Management](#database-session-management)
    - [Database Migrations](#database-migrations)
  - [Consumer Logic & Reliability](#consumer-logic--reliability)
    - [Atomic State Transitions](#atomic-state-transitions)
    - [Error Resilience](#error-resilience)
  - [Caching Strategy](#caching-strategy)
  - [Rate Limiting Strategy](#rate-limiting-strategy)
  - [Logging & Debugging Strategy](#logging--debugging-strategy)
  - [Resilience Strategy](#resilience-strategy)
    - [Infrastructure vs Business Logic Errors](#infrastructure-vs-business-logic-errors)
    - [Dead-Letter Queue DLQ](#dead-letter-queue-dlq)
    - [Retry Backoff Strategy](#retry-backoff-strategy)
      - [Initial Approach](#initial-approach)
      - [Final Approach](#final-approach)
    - [Idempotent Manual Replays](#idempotent-manual-replays)
    - [Job/Task Timeout Handling](#jobtask-timeout-handling)

<!-- /TOC -->

---

## Status During Task Processing

### **Explicit State Machine (IN_PROGRESS)**

- **Decision**: Added an explicit `IN_PROGRESS` state to the job lifecycle (`PENDING` -> `IN_PROGRESS` -> `COMPLETED`/`FAILED`).
- **Rationale**:
  - **Observability**: Distinguishes between jobs waiting in the queue and jobs currently burning CPU/GPU resources.
  - **Zombie Detection**: Allows for monitoring scripts to identify tasks "stuck" in processing (e.g., `updated_at` > 30 mins with `IN_PROGRESS` status), which is impossible with only a `PENDING` state.
- **Trade-offs**:
  - Introduces one additional database write per task.
  - Managed via **Atomic Updates** (SQL `UPDATE` statements) to minimize performance impact.

---

## Database Related Decisions

### **Validation Strategy: The "Validation Tax"**

- **Decision**: Pydantic at the boundaries, Dataclasses for internal data.
- **Rationale**: Pydantic is used for strict validation of incoming API requests and outgoing responses.
Internally, `dataclasses` with `slots=True` are used to avoid the "validation tax" (recursive type checking overhead), significantly reducing CPU cycles and memory footprint.

### **Database Indexing Strategy**

- **Decision**: Implemented **Composite Indexes** at the table level.
- **Implementation**: `Index("ix_tasks_status_created_at", "status", "created_at")`.
- **Rationale**: Individual indexes on `status` and `created_at` are insufficient for high-load pagination.
  - A composite index allows PostgreSQL to perform **Index-Only Scans**, ensuring $O(log N)$ retrieval speed for the dashboard even as the table scales into millions of rows.

### **Database Session Management**

- **Decision**: Async-first connection pooling via `asyncpg`.
- **Implementation**: Leveraged `AsyncDatabasePool` with `pool_size` and `max_overflow` tuned for 100 RPS.
- **Pattern**: Used `@asynccontextmanager` for manual session handling in background consumers to ensure strict lifecycle control and zero connection leaks.

### **Database Migrations**

- **Decision**: Asynchronous Alembic implementation.
- **Rationale**: Refactored `env.py` to use a `run_sync` bridge. This allows the use of the same `asyncpg` driver for migrations and the application, ensuring consistency across development and production environments.

---

## Consumer Logic & Reliability

### **Atomic State Transitions**

- **Decision**: Replaced "Fetch-Modify-Save" (2 round-trips) with **Atomic Updates** (1 round-trip).
- **Rationale**: The consumer uses `UPDATE tasks SET status = :s WHERE task_id = :tid`. This prevents "Lost Updates" in a concurrent environment and reduces DB round-trip latency.

### **Error Resilience**

- **Decision**: Nested `try/except` blocks within the RabbitMQ consumer.
- **Rationale**: Errors in the AI/ML callback are caught and logged to the `error_message` field in the DB, while DB-connection errors are allowed to propagate to trigger RabbitMQ requeueing logic (ensuring zero message loss).

---

## Caching Strategy

- **Redis Integration**: Implemented for `GET` operations on task details.
- **Freshness Policy**: 300s (5-minute) TTL.
- **Constraint**: **No Caching for 404s.** To prevent "Creation Lag," the system does not cache "Resource Not Found" responses, ensuring that a user can see a newly created task immediately upon the first successful fetch.

---

## Rate Limiting Strategy

- **Decision**: Distributed Rate Limiting via Redis.
- **Rationale**: At 100 RPS, in-memory rate limiting fails across multiple workers; using Redis with `slowapi` enforces global rate limits consistently across all instances.

---

## Logging & Debugging Strategy

- **Decision**: Tier 3 Object Storage (S3-Compatible) for execution logs.
- **Rationale**:
  - **Database Performance**: Prevents "Database Bloat." Storing large text blobs in Postgres degrades index performance and increases VACUUM overhead (extra work the database must do to reclaim storage).
  - **Scalability**: Decouples log storage from transaction processing. S3 provides extremely high (99.999999999%) durability and handle infinite horizontal scaling.
- **Implementation**: Workers upload logs to `{task_id}.log` upon completion; API provides a streaming endpoint to retrieve logs on-demand.

---

## Resilience Strategy

### Infrastructure vs Business Logic Errors

- **Infrastructure Errors**: Transient issues outside developer control (e.g., API downtime, DB timeouts).
  - Strategy: Route failed message to a `wait` queue with TTL (no consumers attached) to allow infrastructure time to recover, preventing immediate requeue floods.

- **Business Logic Errors**: Bugs or invalid data in code (e.g., ValueError, logic errors).
  - Strategy: Send directly to Dead-Letter Queue (DLQ) for human review. No retries, as they cannot resolve the issue.

### Dead-Letter Queue (DLQ)

- **Decision**: Added DLQ for messages exceeding retry limits.
- **Rationale**:
  - Preserves undeliverable messages for inspection.
  - Isolates poison messages, preventing main queue blockage.
  - Failed messages are acknowledged (not requeued).
- **Implementation**: Main queue configured with `x-dead-letter-exchange` to route exhausted retries to DLQ.
  - When message.nack(requeue=False) is called after max retries, message moves to DLQ.

### Retry Backoff Strategy

#### Initial Approach

- In-memory exponential backoff in consumer code.
- Rationale: Avoids thundering herd on transient failures; gives issues time to resolve.

#### Final Approach

- **Decision**: Moved retry logic to RabbitMQ using TTL + dead-letter routing.
- **Rationale**:
  - Keeps consumers stateless and simple.
  - Centralizes retry timing and state.
  - Survives worker restarts/scaling.
- **Implementation**:
  - Created `wait` queue with `x-message-ttl`.
  - On failure, publish message to `wait` queue with retry count in headers.
  - After TTL expires, message dead-letters back to main queue.
  - After max retries, message routes to DLQ.

### Idempotent Manual Replays

- **Decision**: Implemented idempotent retries for manual jobs replays in DLQ via API.
- **Rationale**:
  - Users may need to manually retry failed jobs without creating duplicates.
  - Ensures system stability and data integrity during retries.
  - **Implementation**:
    - Added API endpoints for retrying individual jobs and batches.
    - The retry count is reset on manual retries to allow fresh attempts.
    - Instead of storing the entire message in memory while searching DLQ, a `temporary queue` is created to fetch and process messages one at a time, ensuring low memory usage even with large DLQs.

### Job/Task Timeout Handling

- **Decision**: Introduced a configurable timeout for task processing.
- **Rationale**:
  - Prevents tasks from hanging indefinitely due to unforeseen issues (e.g., external service timeouts, infinite loops).
  - Timeouts are treated as transient (retry worthy) failures, allowing for retries. To prevent infinite retry loops, a maximum retry limit is enforced.
- **Implementation**:
  - Added `tasks_timeout` configuration parameter (default: 300 seconds).
  - Wrapped the main task processing logic in `asyncio.wait_for()` to enforce the timeout.
  - On timeout, the task is marked as `FAILED` with an appropriate error message logged
  - **Stuck Tasks Cleanup**: A background script (`tasks_cleanup.py`) periodically removes tasks that have exceeded a defined expiration period (e.g., 6 minutes) to maintain database hygiene and prevent clutter from abandoned tasks.
  