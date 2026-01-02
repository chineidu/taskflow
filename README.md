# TaskFlow

TaskFlow - A Learning-Focused Job Orchestrator

## Table of Contents

<!-- TOC -->

- [TaskFlow](#taskflow)
  - [Table of Contents](#table-of-contents)
  - [Database Migrations](#database-migrations)
    - [Alembic Setup](#alembic-setup)
    - [Create a new Migration](#create-a-new-migration)
    - [Apply Migrations](#apply-migrations)
    - [Rollback Migrations](#rollback-migrations)
    - [Current Revision](#current-revision)
    - [Check Migration History](#check-migration-history)

<!-- /TOC -->

## Database Migrations

### Alembic Setup

- Initialize Alembic (if not already done):

```bash
alembic init alembic
```

- Configure `alembic/env.py` with your database URL.

```py
# =============================================================
# ==================== Add DB Config ==========================
# =============================================================
config.set_main_option("sqlalchemy.url", app_settings.database_url)

...

# ============ Fiter out unneeded metadata ============
excluded_tables: set[str] = {"celery_taskmeta"}
# Only include tables not in excluded_tables for Alembic migrations
filtered_metadata = MetaData()
for table_name, table in Base.metadata.tables.items():
    if table_name not in excluded_tables:
        table.tometadata(filtered_metadata)
target_metadata = filtered_metadata
```

### Create a new Migration

- `Autogenerate`: Compares your database schema to the SQLAlchemy models and automatically creates a migration script that reflects any differences.

```bash
alembic revision --autogenerate -m "Your migration message"

# E.g.
alembic revision --autogenerate -m "Add users table"

# View the SQL statements that will be executed
alembic revision --autogenerate -m "Add users table" --sql
```

### Apply Migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Apply migrations to a specific revision
alembic upgrade <revision_id>
```

### Rollback Migrations

```bash
# Downgrade one revision
alembic downgrade -1

# Downgrade to a specific revision
alembic downgrade <revision_id>
```

### Current Revision

```bash
alembic current
```

### Check Migration History

```bash
alembic history
```
