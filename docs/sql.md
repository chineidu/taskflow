# SQL To SQLAlchemy

- This document contains the raw SQL commands and the corresponding SQLAlchemy ORM commands used in the project for database migrations and operations.

## Using `scalar/scalars` vs `execute`

- **`session.scalars(stmt)`**: Use when you want ORM objects or a list of single values (e.g., a list of usernames).
  - It automatically "unpacks" the single-element rows returned by the database.

- **`session.scalar(stmt)`**: The "Single Value" shortcut. Use for count(), max(), or fetching a single entity by ID.
  - Returns the first element of the first row, or None if no rows exist.

- **`session.execute(stmt)`**: Use when selecting multiple columns (e.g., id AND status) or when you need metadata like rowcount.
  - Returns a Result object (a collection of Row tuples).
  - Pro Tip: Use `result.mappings().all()` after `execute()` to get a list of dictionaries—perfect for API responses.

| If your Select has... | Example | Use... | Reason |
| :--- | :--- | :--- | :--- |
| **One Entity/Object** | `select(DBTask)` | `.scalar()` | You want a single Python object instance. |
| **One Aggregated Column** | `select(func.count(DBTask.id))` | `.scalar()` | You want a single value (e.g., an integer). |
| **Multiple Columns** | `select(DBTask.id, DBTask.status)` | `.execute()` | SQL returns a row with multiple elements. |
| **Grouped Aggregates** | `select(DBTask.status, func.count())` | `.execute()` | You need the "key" (status) and the "value" (count). |
| **Multiple Mapped Objects** | `select(User, Task).join(...)` | `.execute()` | SQL returns a tuple of objects per row. |

## Basic Examples

### Task By Task ID

**SQL:**

```sql
SELECT tasks.id, tasks.task_id, tasks.payload, tasks.status, tasks.error_message, 
       tasks.has_logs, tasks.log_s3_key, tasks.log_s3_url, tasks.in_dlq, 
       tasks.idempotency_key, tasks.created_at, tasks.started_at, 
       tasks.completed_at, tasks.updated_at
FROM tasks
-- task_id_1 is a parameter placeholder
WHERE tasks.task_id = :task_id_1;
```

**SQLAlchemy ORM:**

```py
stmt = select(DBTask).where(DBTask.task_id == task_id)
tasks = await self.db.scalar(stmt)
```

### Tasks By Creation Time

**SQL:**

```sql
SELECT tasks.id, tasks.task_id, tasks.payload, tasks.status, tasks.error_message, 
       tasks.has_logs, tasks.log_s3_key, tasks.log_s3_url, tasks.in_dlq, 
       tasks.idempotency_key, tasks.created_at, tasks.started_at, 
       tasks.completed_at, tasks.updated_at
FROM tasks
-- created_at_1 and created_at_2 are parameter placeholders
WHERE tasks.created_at >= :created_at_1 AND tasks.created_at <= :created_at_2;
```

**SQLAlchemy ORM:**

```py
try:
    start: datetime = parse(created_after)
    end: datetime = parse(created_before)
except (ValueError, TypeError) as e:
    logger.error(f"Invalid date format passed to query: {e}")
    raise ValueError("Timestamps must be valid ISO 8601 strings.") from e

stmt = select(DBTask).where(
    DBTask.created_at >= start,
    DBTask.created_at <= end,
)
result = await self.db.scalars(stmt)
return list(result.all())
```

## Advanced Examples

### Dashboard Metrics

**SQL:**

```sql
SELECT status, COUNT(id) AS task_count
FROM tasks
GROUP BY status
ORDER BY COUNT(id) DESC;
```

**SQLAlchemy ORM:**

```py
# NB: Use `.execute()` for multi-column queries
status_stmt = select(DBTask.status, func.count(DBTask.id)).group_by(DBTask.status)
status_result = await self.db.execute(status_stmt)
# Returns `status: count`. e.g. {'pending': 10, 'completed': 65, 'inprogress': 20, 'failed': 5}
status_mapping = dict(status_result.all())  # type: ignore

dlq_stmt = (
    select(func.count(DBTask.id))
    # i.e. where tasks are in the DLQ
    .where(DBTask.in_dlq)
    .order_by(func.count(DBTask.id).desc())
)
dlq_result = await self.db.scalar(dlq_stmt)
```

### Average Processing Time Per Task

**SQL:**

```sql
SELECT ROUND(AVG(EXTRACT(EPOCH FROM (completed_at - started_at))), 2) AS avg_seconds
FROM tasks
WHERE status = 'completed' AND 
started_at IS NOT NULL AND completed_at IS NOT NULL AND completed_at >= started_at;
```

**SQLAlchemy ORM:**

```py
# Using `extract("epoch", ...)` extracts the epoch time difference between 
# completed_at and started_at.i.e. final result is in seconds
stmt = select(
    func.round(func.avg(func.extract("epoch", DBTask.completed_at - DBTask.started_at)), 2).label(
        "avg_duration_seconds"
    )
).where(
    DBTask.status == TaskStatusEnum.COMPLETED.value,
    DBTask.completed_at.isnot(None),
    DBTask.started_at.isnot(None),
    DBTask.completed_at >= DBTask.started_at,
)
avg_time = await self.db.scalar(stmt)
return avg_time if avg_time else 0.0
```
