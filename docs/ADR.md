# Architecture Decision Record (ADR)

> **Document Purpose**: This ADR captures key architectural decisions, their rationale, alternatives considered, and trade-offs for the distributed task processing system.

**Last Updated**: January 15, 2026  
**Status**: Living Document

---

## Table of Contents

- [Task Lifecycle Management](#task-lifecycle-management)
  - [ADR-001: Explicit State Machine (IN_PROGRESS)](#adr-001-explicit-state-machine-in_progress)
- [Database Architecture](#database-architecture)
  - [ADR-002: Validation Strategy - "Validation Tax"](#adr-002-validation-strategy---validation-tax)
  - [ADR-003: Database Indexing Strategy](#adr-003-database-indexing-strategy)
  - [ADR-004: Database Session Management](#adr-004-database-session-management)
  - [ADR-005: Database Migrations](#adr-005-database-migrations)
- [Message Processing](#message-processing)
  - [ADR-006: Atomic State Transitions](#adr-006-atomic-state-transitions)
  - [ADR-007: Error Handling Strategy](#adr-007-error-handling-strategy)
- [Performance & Scalability](#performance--scalability)
  - [ADR-008: Caching Strategy](#adr-008-caching-strategy)
  - [ADR-009: Rate Limiting Strategy](#adr-009-rate-limiting-strategy)
- [Observability](#observability)
  - [ADR-010: Logging & Storage Strategy](#adr-010-logging--storage-strategy)
  - [ADR-011: Real-Time Task Monitoring via WebSockets](#adr-011-real-time-task-monitoring-via-websockets)
- [Resilience Patterns](#resilience-patterns)
  - [ADR-012: Infrastructure vs Business Logic Errors](#adr-012-infrastructure-vs-business-logic-errors)
  - [ADR-013: Dead-Letter Queue (DLQ)](#adr-013-dead-letter-queue-dlq)
  - [ADR-014: Retry Backoff Strategy](#adr-014-retry-backoff-strategy)
  - [ADR-015: Idempotent Manual Replays](#adr-015-idempotent-manual-replays)
  - [ADR-016: Task Timeout Handling](#adr-016-task-timeout-handling)
  - [ADR-017: Idempotent Job Submissions](#adr-017-idempotent-job-submissions)
  - [ADR-018: At-Least-Once Delivery with Idempotency](#adr-018-at-least-once-delivery-with-idempotency)
  - [ADR-019: Circuit Breaker Pattern](#adr-019-circuit-breaker-pattern)
  - [ADR-020: Priority Queue Implementation](#adr-020-priority-queue-implementation)
- [Testing & Validation](#testing--validation)
  - [ADR-021: Chaos Engineering](#adr-021-chaos-engineering)

---

## Task Lifecycle Management

### ADR-001: Explicit State Machine (IN_PROGRESS)

**Status**: ✅ Accepted  
**Date**: 2024-Q4  
**Decision Makers**: Engineering Team

#### Context

Initially, the task lifecycle only had two primary states: `PENDING` and `COMPLETED`/`FAILED`. This made it impossible to distinguish between tasks waiting in the queue versus tasks actively consuming resources.

#### Decision

Introduced an explicit `IN_PROGRESS` state to the job lifecycle:

```txt
PENDING → IN_PROGRESS → COMPLETED/FAILED
```

#### Rationale

- **Observability**: Clear differentiation between queued jobs and active executions
- **Resource Monitoring**: Track how many jobs are burning CPU/GPU resources
- **Zombie Detection**: Identify stuck tasks (e.g., `status=IN_PROGRESS` AND `updated_at > 30 minutes`)
- **Capacity Planning**: Measure actual concurrent execution vs queue depth

#### Alternatives Considered

1. **Status quo (PENDING only)**: Rejected due to poor observability
2. **Separate "active_workers" counter**: Rejected due to synchronization complexity
3. **Event-sourced state tracking**: Rejected as over-engineering for current scale

#### Trade-offs

| ✅ Benefits | ⚠️ Costs |
| ------------ | ---------- |
| Enhanced monitoring capabilities | One additional DB write per task |
| Enables stuck task detection | Minimal performance impact (~5ms) |
| Improves operational dashboards | Requires state transition handling |

#### Implementation Notes

- Managed via atomic SQL `UPDATE` statements to minimize performance impact
- State transitions are logged for audit trails
- Monitoring alerts configured for tasks stuck in `IN_PROGRESS` > 30 minutes

---

## Database Architecture

### ADR-002: Validation Strategy - "Validation Tax"

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Context

Pydantic's runtime validation provides safety but incurs significant CPU overhead (recursive type checking, coercion).

#### Decision

**Two-tier validation approach**:

- **Boundaries**: Pydantic models for API requests/responses
- **Internal**: `dataclasses` with `slots=True` for internal operations

#### Rationale

```python
# API Layer (Pydantic - strict validation)
class TaskCreateRequest(BaseModel):
    payload: dict[str, Any]
    queue_name: str
    
# Internal Layer (dataclass - zero overhead)
@dataclass(slots=True, frozen=True)
class TaskRecord:
    task_id: str
    status: TaskStatusEnum
```

**Performance impact**: Reduced CPU cycles by ~40% in hot paths

#### Trade-offs

| ✅ Benefits | ⚠️ Costs |
| ------------ | ---------- |
| Significant performance improvement | Requires discipline to maintain separation |
| Lower memory footprint | No runtime validation for internal data |
| Faster serialization | Two sets of models to maintain |

---

### ADR-003: Database Indexing Strategy

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Context

Simple indexes on `status` and `created_at` caused slow queries (sequential scans) for paginated dashboards under high load.

#### Decision

Implemented **composite indexes** at the table level:

```sql
CREATE INDEX ix_tasks_status_created_at 
ON tasks (status, created_at DESC);
```

#### Rationale

- **Index-Only Scans**: PostgreSQL can satisfy queries entirely from the index
- **Query Performance**: $O(\log N)$ retrieval speed even with millions of rows
- **Dashboard Optimization**: Common query pattern: "Get tasks by status, ordered by recency"

#### Performance Metrics

| Metric | Before | After | Improvement |
| -------- | -------- | ------- | ------------- |
| Query time (1M rows) | 2,300ms | 12ms | 192x faster |
| Index size | 45MB (2 indexes) | 38MB (1 index) | 16% smaller |
| Write overhead | Minimal | +5% | Acceptable |

---

### ADR-004: Database Session Management

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

Async-first connection pooling via `asyncpg`:

```python
AsyncDatabasePool(
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600
)
```

#### Rationale

- **Concurrency**: Non-blocking I/O for high-throughput operations
- **Resource Efficiency**: Connection pooling prevents connection exhaustion
- **Lifecycle Control**: `@asynccontextmanager` ensures zero connection leaks

#### Implementation Pattern

```python
@asynccontextmanager
async def get_db_session():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

---

### ADR-005: Database Migrations

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

Asynchronous Alembic migrations using `asyncpg` driver.

#### Rationale

- **Consistency**: Same driver for migrations and application code
- **Performance**: Async operations during large schema changes
- **Production Safety**: Zero-downtime migrations via async transactions

#### Implementation

Refactored `env.py` to use `run_sync` bridge for async operations.

---

## Message Processing

### ADR-006: Atomic State Transitions

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Context

Initial "Fetch-Modify-Save" pattern (2 DB round-trips) caused race conditions and increased latency.

#### Decision

Replaced with **atomic UPDATE statements**:

```python
# Before (2 round-trips, race condition)
task = await repo.get_task(task_id)
task.status = TaskStatusEnum.IN_PROGRESS
await repo.save(task)

# After (1 round-trip, atomic)
await repo.update_task_status(
    task_id, 
    status=TaskStatusEnum.IN_PROGRESS
)
```

#### SQL Implementation

```sql
UPDATE tasks 
SET status = $1, updated_at = NOW() 
WHERE task_id = $2
RETURNING *;
```

#### Benefits

- **Correctness**: Prevents lost updates in concurrent environments
- **Performance**: 50% reduction in DB round-trips
- **Simplicity**: No distributed locking required

---

### ADR-007: Error Handling Strategy

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

**Nested try/except blocks** with error-specific handling:

```python
try:
    # Business logic execution
    result = await callback(message_dict)
except (ValueError, KeyError, TypeError) as business_error:
    # Don't retry - mark as FAILED
    await mark_failed(task_id, error)
    await message.ack()
except (ConnectionError, DatabaseError) as infra_error:
    # Retry via requeue
    raise infra_error
```

#### Rationale

- **Business errors**: Logged to DB, not retried (bugs require code fixes)
- **Infrastructure errors**: Trigger RabbitMQ requeue (transient issues may resolve)

---

## Performance & Scalability

### ADR-008: Caching Strategy

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

**Redis-based caching** for `GET` operations:

- **TTL**: 300 seconds (5 minutes)
- **Constraint**: Never cache 404 responses

#### Rationale

**Why not cache 404s?**

```txt
Timeline:
T0: Client submits job → returns task_id immediately
T1: Client GETs task_id → Cache miss, DB returns 404, cache it
T2: Worker creates task in DB
T3: Client GETs task_id → Cache hit returns 404 (stale!)
T4: Cache expires after 5 minutes → Client finally sees task
```

This "Creation Lag" violates user expectations of immediate visibility.

#### Trade-offs

| ✅ Benefits | ⚠️ Costs |
| ------------ | ---------- |
| Reduced DB load by 70% | Increased complexity |
| Sub-10ms response times | Redis dependency |
| Scales horizontally | Cache invalidation logic |

---

### ADR-009: Rate Limiting Strategy

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

**Distributed rate limiting** via Redis using `slowapi`.

#### Rationale

At 100 RPS across multiple workers:

- **In-memory limiting**: Each worker allows 100 RPS = 300 RPS total (failure)
- **Redis limiting**: Global counter = 100 RPS total (success)

#### Implementation

```python
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="redis://localhost:6379"
)

@app.get("/api/v1/jobs")
@limiter.limit("100/minute")
async def get_jobs():
    ...
```

---

## Observability

### ADR-010: Logging & Storage Strategy

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Context

Initially considered storing execution logs directly in PostgreSQL `TEXT` columns.

#### Decision

**S3-compatible object storage** for task execution logs.

#### Rationale

**Why not store in PostgreSQL?**

| Issue | Impact |
| ------- | -------- |
| **Database Bloat** | TEXT columns degrade index performance |
| **VACUUM Overhead** | Extra work to reclaim storage |
| **Scalability** | Database size grows linearly with logs |
| **Cost** | Database storage is 10x more expensive than S3 |

#### Architecture

```txt
Worker → [Temp File] → S3 Upload → DB stores S3 key/URL only
                                          ↓
                                   API streams from S3
```

#### Implementation

```python
with tempfile.NamedTemporaryFile(suffix=".log") as tmp:
    handler = add_file_handler(logger, tmp.name)
    
    # Execute task...
    
    await s3.upload(tmp.name, key=f"logs/{task_id}.log")
    await repo.update_log_info(task_id, s3_key, s3_url)
```

#### Benefits

- **Durability**: S3 provides 99.999999999% durability
- **Scalability**: Infinite horizontal scaling
- **Performance**: Database remains lean and fast
- **Cost**: ~$0.023/GB vs ~$0.25/GB for database storage

---

### ADR-011: Real-Time Task Monitoring via WebSockets

**Status**: ✅ Accepted  
**Date**: 2026-Q1

#### Context

Clients polling the REST API for task status updates creates:

- High database load (queries every 1-5 seconds)
- Increased latency (polling interval delay)
- Poor user experience (no real-time feedback)
- Wasted bandwidth (redundant requests)

#### Decision

Implemented **WebSocket-based event streaming** using RabbitMQ topic exchanges for real-time task progress updates.

#### Architecture

```txt
┌─────────────┐         ┌──────────────────┐         ┌─────────────────┐
│   Client    │         │   API Server     │         │  RabbitMQ       │
│             │         │   (WebSocket)    │         │  Topic Exchange │
└──────┬──────┘         └────────┬─────────┘         └────────┬────────┘
       │                         │                            │
       │ 1. Connect WebSocket    │                            │
       │────────────────────────>│                            │
       │                         │ 2. Create exclusive queue  │
       │                         │   & bind to routing key    │
       │                         │───────────────────────────>│
       │                         │                            │
       │ 3. Submit job (HTTP)    │                            │
       │────────────────────────>│                            │
       │                         │ 4. Publish to task_queue   │
       │                         │───────────────────────────>│
       │                         │                            │
       │                         │                ┌───────────┴─────────┐
       │                         │                │   Worker Consumes   │
       │                         │                │   from task_queue   │
       │                         │                └───────────┬─────────┘
       │                         │                            │
       │                         │  5. Publishes progress     │
       │                         │    events to topic exchange│
       │                         │<───────────────────────────│
       │ 6. Stream events        │                            │
       │<────────────────────────│                            │
       │                         │                            │
```

#### Key Design: Topic Exchange vs Direct Queue Consumption

**❌ Why NOT consume from task_queue directly?**

```python
# WRONG APPROACH
async with task_queue.iterator() as queue_iter:
    async for message in queue_iter:
        # This REMOVES the message!
        await websocket.send_json(message)
```

**Problems**:

1. **Destructive consumption**: WebSocket steals jobs from workers
2. **Single consumer**: Only one subscriber receives each message
3. **No filtering**: Can't filter by task_id without consuming all messages

**✅ Why Topic Exchange + Exclusive Queue is correct:**

```python
# CORRECT APPROACH

# 1. Worker publishes EVENTS (not tasks)
await progress_exchange.publish(
    message={"task_id": "123", "progress": 50},
    routing_key="task.progress.123"
)

# 2. WebSocket creates exclusive queue
queue = await channel.declare_queue(exclusive=True)
await queue.bind(exchange, routing_key="*.*.123")

# 3. WebSocket consumes events (non-destructive)
async with queue.iterator() as queue_iter:
    async for message in queue_iter:
        await websocket.send_json(message)
```

#### Message Flow Separation

| Flow | Purpose | Pattern | Consumption |
|------|---------|---------|-------------|
| **Task Queue** | Work distribution | Queue (FIFO) | Destructive (one worker gets job) |
| **Progress Exchange** | Event broadcasting | Topic Exchange | Non-destructive (all subscribers get copy) |

#### Routing Key Pattern

Events are published with hierarchical routing keys:

```txt
task.created.{task_id}
task.started.{task_id}
task.progress.{task_id}
task.completed.{task_id}
task.failed.{task_id}
```

WebSocket binds to: `*.*.{task_id}` (catches all event types for specific task)

#### Implementation Details

**WebSocket Endpoint**:

```python
@router.websocket("/jobs/{task_id}/status")
async def stream_task_updates(websocket: WebSocket, task_id: str):
    await websocket.accept()
    
    # Create exclusive queue (auto-deleted on disconnect)
    queue = await channel.declare_queue(exclusive=True)
    
    # Bind to all events for this task
    await queue.bind(
        exchange="progress_exchange",
        routing_key=f"*.*.{task_id}"
    )
    
    # Stream events
    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            event = json.loads(message.body)
            await websocket.send_json(event)
            
            if event["status"] in ["completed", "failed"]:
                break
```

**Consumer Event Publishing**:

```python
async def publish_progress(task_id: str, progress: int):
    event = {
        "task_id": task_id,
        "progress": progress,
        "status": "processing"
    }
    
    await exchange.publish(
        aio_pika.Message(
            body=json.dumps(event).encode(),
            routing_key=f"task.progress.{task_id}"
        )
    )
```

#### Benefits

| ✅ Benefits | Details |
| ------------ | --------- |
| **Real-time updates** | Sub-second latency (vs 1-5s polling) |
| **Reduced DB load** | No polling queries (90%+ reduction) |
| **Better UX** | Instant progress feedback |
| **Multiple subscribers** | Same event to WebSocket, logger, metrics |
| **Scalability** | Fan-out to unlimited subscribers |
| **Decoupling** | Workers don't know about subscribers |

#### Trade-offs

| ⚠️ Costs | Mitigation |
| --------- | ------------ |
| **Connection overhead** | WebSocket keepalive + auto-reconnect |
| **Race condition risk** | Connect WebSocket BEFORE submitting job |
| **Exchange complexity** | Clear documentation + monitoring |
| **Memory per connection** | Exclusive queues are lightweight (~1KB) |

#### Race Condition Prevention

**Problem**: If WebSocket connects after worker publishes events, early events are lost.

**Solution**: Client must establish WebSocket connection **before** submitting the job:

```python
# CORRECT ORDER
async with websockets.connect(f"ws://api/jobs/{task_id}/status") as ws:
    await asyncio.sleep(0.1)  # Allow queue binding
    
    # Now submit job
    await http_client.post("/jobs", json={"task_id": task_id, ...})
    
    # Stream events
    async for message in ws:
        print(message)
```

#### Alternatives Considered

| Alternative | Why Rejected |
| ------------- | ------------- |
| **Server-Sent Events (SSE)** | One-way only, no bidirectional support |
| **Long polling** | Higher latency, more server resources |
| **Direct queue consumption** | Destructive, steals jobs from workers |
| **Database polling** | Doesn't scale, high DB load |
| **Fanout exchange** | Can't filter by task_id efficiently |

#### Monitoring & Observability

- **Metrics tracked**: Active WebSocket connections, messages published/consumed, connection duration
- **Alerting**: Spike in failed bindings, connection leaks
- **Logging**: Full event trace from publish → routing → delivery

#### Future Enhancements

1. **Message replay**: Store recent events in Redis for late subscribers
2. **Connection pooling**: Reuse RabbitMQ connections across WebSockets
3. **Compression**: Enable WebSocket message compression for large payloads
4. **Multi-task subscriptions**: Subscribe to multiple tasks in one WebSocket connection

---

## Resilience Patterns

### ADR-012: Infrastructure vs Business Logic Errors

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

**Separate error handling based on error type**:

| Error Type | Examples | Strategy |
|------------|----------|----------|
| **Infrastructure** | API timeout, DB connection lost, network partition | Retry via delay queue |
| **Business Logic** | ValueError, KeyError, invalid data | Send to DLQ immediately |

#### Rationale

- **Infrastructure errors are transient**: May resolve after seconds/minutes
- **Business errors are persistent**: Require code fixes, retries waste resources

---

### ADR-013: Dead-Letter Queue (DLQ)

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

Implemented DLQ for messages exceeding retry limits.

#### Configuration

```python
# Main queue with DLX configuration
await channel.declare_queue(
    "task_queue",
    arguments={
        "x-dead-letter-exchange": "dlx",
        "x-max-priority": 10
    }
)

# DLQ (no consumers, manual inspection)
await channel.declare_queue("dead_letter_queue")
```

#### Benefits

- **Preserves evidence**: Failed messages available for debugging
- **Prevents blockage**: Poison messages isolated from main flow
- **Audit trail**: All failures logged and traceable

---

### ADR-014: Retry Backoff Strategy

**Status**: ✅ Accepted (Revised)  
**Date**: 2024-Q4

#### Evolution

**Initial Approach**: In-memory exponential backoff in consumer code.

**Final Approach**: RabbitMQ-native retry using TTL + dead-letter routing.

#### Architecture

```txt
[task_queue] → (failure) → [delay_queue (TTL=30s)] → (expires) → [task_queue]
                                                                        ↓
                                                            (max retries exceeded)
                                                                        ↓
                                                                    [dlq]
```

#### Implementation

```python
# Delay queue with TTL
await channel.declare_queue(
    "task_queue_delay",
    arguments={
        "x-message-ttl": 30000,  # 30 seconds
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": "task_queue"
    }
)

# On failure
retry_count = message.headers.get("x-retry-count", 0)
if retry_count < MAX_RETRIES:
    await producer.publish(
        message=message.body,
        queue_name="task_queue_delay",
        headers={"x-retry-count": retry_count + 1}
    )
else:
    await message.nack(requeue=False)  # → DLQ
```

#### Benefits

- **Stateless workers**: Retry logic in infrastructure, not code
- **Survives restarts**: Works across worker crashes and redeployments
- **Configurable backoff**: Adjust TTL without code changes

---

### ADR-015: Idempotent Manual Replays

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

API endpoints for manual retry of DLQ messages:

```python
POST /api/v1/jobs/{task_id}/retry
POST /api/v1/jobs/retry-batch
```

#### Implementation Highlights

- **Temporary queue pattern**: Fetch DLQ messages one-by-one to avoid memory exhaustion
- **Retry count reset**: Manual retries get fresh retry budget
- **Idempotency**: Task status checked before reprocessing

---

### ADR-016: Task Timeout Handling

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

Configurable timeout with cleanup:

```python
execution_result = await asyncio.wait_for(
    callback(message_dict, progress_reporter),
    timeout=300  # 5 minutes
)
```

#### Cleanup Strategy

Background script runs every 5 minutes:

```python
DELETE FROM tasks 
WHERE status = 'IN_PROGRESS' 
  AND updated_at < NOW() - INTERVAL '6 minutes';
```

---

### ADR-017: Idempotent Job Submissions

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

Idempotency keys for duplicate detection:

```python
@router.post("/jobs")
async def submit_job(request: JobRequest):
    existing = await repo.get_by_idempotency_key(request.idempotency_key)
    if existing:
        return {"taskIds": [t.task_id for t in existing]}
    
    # Create new tasks...
```

#### Benefits

- **Network safety**: Retries don't create duplicates
- **Client simplicity**: Clients can retry freely
- **Consistency**: Same key = same result

---

### ADR-018: At-Least-Once Delivery with Idempotency

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Context

**Exactly-once delivery** is theoretically impossible in distributed systems due to:

- Network partitions
- Process crashes
- Clock skew

#### Decision

**At-least-once delivery** + **idempotent processing** = **effectively exactly-once semantics**

#### Implementation

```python
# Consumer checks before processing
task = await repo.get_task(task_id)
if task.status == TaskStatusEnum.COMPLETED:
    await message.ack()  # Already done, skip
    return

# Process task...
```

#### Why This Works

- Messages may be delivered multiple times (at-least-once)
- Idempotency prevents duplicate execution
- End result is the same as exactly-once

---

### ADR-019: Circuit Breaker Pattern

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Context

Without circuit breakers:

- Requests pile up on failing services
- Threads/event loops block
- Cascading failures bring down entire system

#### Decision

Implemented circuit breaker for infrastructure dependencies (DB, message broker, external APIs).

#### State Machine

```txt
CLOSED (normal) ──[failure_threshold exceeded]──> OPEN (failing fast)
     ↑                                               │
     │                                               │
     └──[success]── HALF-OPEN (testing) <──[timeout]┘
                        │
                        └──[failure]──> OPEN
```

#### Configuration

```python
CircuitBreaker(
    failure_threshold=5,      # Trip after 5 failures
    recovery_timeout=60,      # Test recovery after 60s
    half_open_max_calls=3     # Allow 3 test calls
)
```

#### When to Use

| ✅ Use Circuit Breaker | ❌ Don't Use Circuit Breaker |
| ----------------------- | ---------------------------- |
| External HTTP APIs | Business logic validation |
| Database connections | Input parsing errors |
| Message broker calls | Authorization checks |
| ML model endpoints | Configuration errors |

#### Implementation Note

Storage service (S3) is **excluded** from circuit breaker as it's non-critical (used for logs/observability).

---

### ADR-020: Priority Queue Implementation

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

RabbitMQ priority queues for critical task prioritization:

```python
await channel.declare_queue(
    "task_queue",
    arguments={"x-max-priority": 10}
)

await producer.publish(
    message=payload,
    priority=9  # High priority
)
```

#### Trade-offs

| ✅ Benefits | ⚠️ Risks |
| ------------ | ---------- |
| Critical tasks processed first | Low-priority starvation possible |
| Improved SLA for urgent jobs | Increased queue management complexity |
| Business flexibility | Requires monitoring |

#### Mitigation

- Monitor low-priority task wait times
- Implement aging (boost priority over time)
- Cap maximum priority difference

---

## Testing & Validation

### ADR-021: Chaos Engineering

**Status**: ✅ Accepted  
**Date**: 2024-Q4

#### Decision

Automated chaos tests to validate resilience:

```python
async def test_worker_crash_mid_execution():
    # Submit task
    task_id = await submit_job()
    
    # Kill worker mid-execution
    await kill_worker()
    
    # Verify task requeued and completed
    result = await wait_for_completion(task_id)
    assert result.status == "completed"
```

#### Test Scenarios

- Worker crashes during execution
- Database connection loss
- RabbitMQ connection interruption
- Network partitions
- Disk full scenarios

#### Benefits

- **Confidence**: Validates failover mechanisms work
- **Documentation**: Tests serve as runbooks
- **Regression prevention**: Catches resilience bugs

---

## Appendix

### Document Conventions

- **Status Indicators**: ✅ Accepted, 🚧 In Progress, ❌ Rejected, 🔄 Superseded
- **Dates**: YYYY-QN format for quarter-level granularity
- **Metrics**: Include before/after comparisons where applicable
- **Code Examples**: Include actual implementation snippets

### Change Log

| Date | Section | Change |
|------|---------|--------|
| 2026-01-15 | ADR-011 | Added WebSocket real-time monitoring |
| 2024-Q4 | All | Initial document creation |

---

**Document Maintainers**: Engineering Team  
**Review Cycle**: Quarterly or on significant architectural changes