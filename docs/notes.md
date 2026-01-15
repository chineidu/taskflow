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
    - [Idempotent Job Submissions](#idempotent-job-submissions)
    - [Exactly-once vs At-least-once Delivery](#exactly-once-vs-at-least-once-delivery)
      - [At-least-once delivery](#at-least-once-delivery)
      - [Exactly-once delivery](#exactly-once-delivery)
    - [Chaos Engineering](#chaos-engineering)
      - [Resuming Failed Tasks](#resuming-failed-tasks)
    - [Priority Queue](#priority-queue)
  - [Circuit Breaker Pattern](#circuit-breaker-pattern)
    - [Core Concept Mental Model](#core-concept-mental-model)
      - [States](#states)
    - [State Transitions](#state-transitions)
    - [When Should Circuit Breakers Be Used?](#when-should-circuit-breakers-be-used)
    - [Progress Reporting Callback](#progress-reporting-callback)

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

- Dead letter queue is a secondary queue that holds messages that cannot be processed successfully after a certain number of attempts.
- Retriable (transient errors) messages are retried a configurable number of times before being sent to the DLQ.
- Poison messages (non-retriable errors) or logic errors are sent directly to the DLQ without retries.

---
---

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
  
### Idempotent Job Submissions

- **Decision**: Enabled idempotent job submissions using idempotency keys.
- **Rationale**:
  - Prevents duplicate job creation in scenarios where clients may retry submissions due to network issues or timeouts.
  - Ensures that multiple submissions with the same idempotency key return the same result without creating duplicate tasks.
- **Implementation**:
  - Each job submission requires a unique `idempotency_key`.
  - The system checks for existing tasks with the same idempotency key (using a suffix added to the original key to handle batch submissions).
  - If existing tasks are found, the API returns their IDs and status instead of creating new tasks.
  - This logic is implemented in both the API route handling job submissions and the RabbitMQ consumer to ensure consistency across different submission methods.

### Exactly-once vs At-least-once Delivery

#### At-least-once delivery

- The message is guaranteed to be delivered at least one time, but may be delivered multiple times.
- If there's a failure before acknowledgment, the message broker will redeliver.
- Simpler to implement but requires idempotent consumers.
- Common in most messaging systems (RabbitMQ, Kafka, etc.).
- This project implements `at-least-once delivery` with `idempotency`, which achieves effectively exactly-once processing.

#### Exactly-once delivery

- The message is guaranteed to be delivered and processed exactly one time, no duplicates.
- Much harder to achieve in distributed systems.
- Requires distributed transactions or sophisticated deduplication mechanisms.
- True exactly-once is theoretically impossible in distributed systems.

### Chaos Engineering

#### Resuming Failed Tasks

- **Decision**: Implemented chaos engineering tests to validate system resilience.
- **Rationale**:
  - To ensure the system can handle unexpected failures gracefully and maintain data integrity.
  - To validate that tasks can be resumed or retried successfully after failures.
- **Implementation**:
  - Developed tests that simulate worker crashes mid-execution, verifying that tasks are requeued and successfully completed upon retry.
  - The consumer checks the status of tasks before processing to ensure idempotency and prevent duplicate executions.

### Priority Queue

- **Decision**: Implemented priority-based message handling in RabbitMQ.
- **Rationale**:
  - Certain tasks may require expedited processing due to their critical nature.
  - Priority queues allow high-priority tasks to be processed before lower-priority ones, improving overall system responsiveness for urgent jobs.
- **Implementation**:
  - Configured RabbitMQ queues with `x-max-priority` to enable priority handling.
  - Messages are published with a `priority` field, where higher numerical values indicate higher priority.
  - The consumer processes messages based on their priority, ensuring that critical tasks are addressed promptly.
- **Trade-offs**:
  - Increased complexity in message publishing and queue management.
  - Potential for starvation of low-priority tasks if high-priority tasks are continuously submitted.

## Circuit Breaker Pattern

- The Circuit Breaker pattern is a resilience pattern used to prevent cascading failures when your service depends on an unreliable external system (API, DB, message broker, ML model service, etc.).

- Without a circuit breaker:
  - Requests keep going to the failing service
  - Threads/event loop get blocked
  - Latency spikes
  - Eventually your whole service goes down
  
- With the circuit breaker, the system fails fast and recovers gracefully.

### Core Concept (Mental Model)

It's similar to a real electrical circuit breaker.

#### States

- **CLOSED (Normal)**
  - Requests flow through
  - Failures are counted

- **OPEN (Tripped)**
  - Requests are immediately rejected
  - No calls made to the failing service

- **HALF-OPEN (Recovery test)**
  - A few trial requests are allowed
  - If they succeed -> go back to CLOSED
  - If they fail -> back to OPEN

### State Transitions

```txt
CLOSED         -> (failures exceed threshold)      -> OPEN
OPEN           -> (timeout expires)                -> HALF-OPEN
HALF-OPEN      -> (success)                        -> CLOSED
HALF-OPEN      -> (failure)                        -> OPEN

```

### When Should Circuit Breakers Be Used?

- Use a circuit breaker when calling an external service that can lead to cascading failures in your system or infrastructure errors.
  - Examples:
    - External HTTP APIs
    - Database connections
    - Message brokers
    - ML model serving endpoints, etc.

- Don't use a circuit breaker for business logic errors (e.g., invalid input data) that should be handled gracefully without tripping the circuit.

- **Implementation**:
  - Integrated a circuit breaker mechanism in the consumer service when using infrastructure resources. i.e. not business logic
  - Even though storage service is part of infrastructure resources, it's not a critical component (used for storing logs and observability) and it's not integrated with the circuit breaker.
  - Configured thresholds for failure rates and timeouts to trip the circuit breaker appropriately.

### Progress Reporting Callback

- **Decision**: This system treats progress reporting as part of task state management and not best-effort reporting.
- **Rationale**:
  - Progress updates are critical for monitoring long-running tasks.
  
- **Implementation**:
  - The consumer accepts a callback function that reports progress percentage.
  - This callback is invoked periodically during task execution to update the task's progress in the database.
  