# TaskFlow

TaskFlow is a robust, learning-focused job orchestrator built with high-performance Python technologies.

It demonstrates a modern, scalable architecture for asynchronous task processing, utilizing a microservices-ready approach with FastAPI, RabbitMQ, and PostgreSQL.

## 🚀 Overview

TaskFlow serves as a powerful backend system designed to handle job submissions via a REST API, queue them for asynchronous processing, and reliably execute them using background consumers.

It handles idempotency, rate limiting, logging, and failures gracefully, making it a solid foundation for building scalable distributed systems.

## ✨ Key Features

- **Asynchronous Task Processing**: Decouples job submission from execution using RabbitMQ.
- **High Performance API**: Built with **FastAPI** and modern async Python drivers.
- **Reliable Messaging**: Uses **RabbitMQ** with Dead Letter Queues (DLQ) and retry mechanisms.
- **Idempotency**: Prevents duplicate job execution using unique idempotency keys.
- **Rate Limiting**: Protects the API using **SlowAPI** (Token Bucket algorithm).
- **Data Persistence**: Stores job states and results in **PostgreSQL** using **SQLAlchemy** (Async).
- **Caching**: Implements **Redis** caching for improved performance.
- **Scalable Architecture**: Docker-ready with `docker-compose` for easy orchestration.
- **Comprehensive Logging**: Centralized logging with potential for S3 storage (MinIO).
- **Load Testing**: Includes **Locust** scripts for performance benchmarking.

## 🛠️ Tech Stack

- **Language**: Python 3.13+
- **Web Framework**: FastAPI
- **Database**: PostgreSQL (Asyncpg + SQLAlchemy)
- **Message Broker**: RabbitMQ (aio-pika)
- **Caching**: Redis (aiocache)
- **Object Storage**: MinIO (S3 compatible)
- **Migrations**: Alembic
- **Testing**: Pytest, Locust
- **Dependency Management**: uv
- **Containerization**: Docker, Docker Compose

## 🏗️ Architecture

The system consists of several key components:

1. **API Service**: Accepts job submissions and queries. It handles validation, database records creation, and publishing messages to RabbitMQ.
2. **RabbitMQ**: Acts as the message broker, buffering tasks to be processed.
3. **Consumer Service (Worker)**: Listens to queues, processes tasks, updates their status in the database, and handles retries/failures.
4. **Database**: PostgreSQL stores the reliable state of tasks and system metadata.
5. **Redis**: Caches frequent requests to reduce load.
6. **MinIO**: Stores logs and other artifacts.

## 🚀 Getting Started

### Prerequisites

- **Docker** and **Docker Compose**
- **Python 3.13+** (if running locally)
- **uv** (recommended for package management) or `pip`

### 🔧 Installation

1. **Clone the repository:**

    ```bash
    git clone https://github.com/yourusername/taskflow.git
    cd taskflow
    ```

2. **Environment Setup:**
    The project uses a `.env` file for configuration. Ensure you have one set up (see `src/config/config.env.example` if available, or check `docker-compose.yaml` for expected variables).

### 🏃‍♂️ Running with Docker (Recommended)

The easiest way to run the entire system is using the provided Makefile.

```bash
# Build and start all services (API, Worker, DB, RabbitMQ, Redis, MinIO)
make up
```

- **API**: Access at `http://localhost:8000`
- **RabbitMQ UI**: Access at `http://localhost:15672` (User: `guest`/`guest`)
- **MinIO Console**: Access at `http://localhost:9001`

To stop the services:

```bash
make down
```

### 💻 Local Development

If you prefer to run the Python services locally while keeping infrastructure in Docker:

1. **Install dependencies**:

    ```bash
    make install
    ```

2. **Start Infrastructure**:
    You may need to start only the support services (DB, Redis, RabbitMQ) via Docker.

    ```bash
    docker-compose up -d database local-rabbitmq redis minio
    ```

3. **Run API**:

    ```bash
    make api-run
    ```

4. **Run Worker**:

    ```bash
    make consumer-run
    ```

## 📖 API Documentation

Once the application is running, you can access the interactive API documentation:

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### Key Endpoints

- `POST /api/v1/jobs`: Submit a new job.
- `GET /api/v1/jobs/{task_id}`: Get job status.
- `GET /api/v1/health`: Health check.
- `GET /api/v1/logs/{task_id}`: Retrieve execution logs.

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

## 🧪 Testing

The project includes unit tests and load tests.

```bash
# Run unit tests
make test

# Run load tests (Locust)
locust -f locustfile.py
```
