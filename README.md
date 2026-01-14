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
- **Interactive Dashboards**: HTMX-powered job views and a **Streamlit** dashboard for real-time monitoring.
- **System Metrics**: Built-in metrics endpoint for monitoring task processing throughput and system health.
- **Idempotency**: Prevents duplicate job execution using unique idempotency keys.
- **Rate Limiting**: Protects the API using **SlowAPI** (Token Bucket algorithm).
- **Data Persistence**: Stores job states and results in **PostgreSQL** using **SQLAlchemy** (Async).
- **Caching**: Implements **Redis** caching for improved performance.
- **DLQ Management**: Dedicated endpoints to inspect and replay failed tasks from the Dead Letter Queue.
- **Automated Cleanup**: Background service for cleaning up expired task records and maintaining database health.
- **Scalable Architecture**: Docker-ready with `docker-compose` for easy orchestration.
- **Comprehensive Logging**: Centralized logging with potential for S3 storage (MinIO).
- **Load Testing**: Includes **Locust** scripts for performance benchmarking.

## 🛠️ Tech Stack

- **Language**: Python 3.13+
- **Web Framework**: FastAPI, Jinja2, HTMX
- **Dashboard**: Streamlit
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
5. **Redis**: Caches frequent requests and handles rate limiting.
6. **MinIO**: Stores logs and other artifacts.
7. **Frontend Dashboard (Streamlit)**: Provides a user-friendly interface for monitoring system health and task metrics.
8. **Tasks Cleanup Service**: A background worker that periodically removes expired task records from the database.
9. **HTMX Templates**: Located in [src/api/templates](src/api/templates), these Jinja2 templates ([jobs.html](src/api/templates/jobs.html), [task_row.html](src/api/templates/task_row.html)) enable reactive UI components served directly by the API.

## 🚀 Getting Started

### Prerequisites

- **Docker** and **Docker Compose**
- **Python 3.13+** (if running locally)
- **uv** (recommended for package management) or `pip`

### 🔧 Installation

1. **Clone the repository:**

    ```bash
    git clone https://github.com/chineidu/taskflow.git
    cd taskflow
    ```

2. **Environment Setup:**
    The project uses a `.env` file for configuration. Copy the example if available or ensure the required variables are set.

    ```bash
    cp .env.example .env  # If example exists
    ```

### 🏃‍♂️ Running with Docker (Recommended)

The easiest way to run the entire system is using the provided Makefile.

```bash
# Build and start all services (API, Worker, DB, RabbitMQ, Redis, MinIO, Frontend)
make up
```

- **API**: [http://localhost:8000](http://localhost:8000)
- **Frontend Dashboard**: [http://localhost:8501](http://localhost:8501) (Streamlit)
- **HTMX Job Demo**: [http://localhost:8000/api/v1/jobs/view/demo](http://localhost:8000/api/v1/jobs/view/demo)
- **RabbitMQ UI**: [http://localhost:15672](http://localhost:15672) (User: `guest`/`guest`)
- **MinIO Console**: [http://localhost:9001](http://localhost:9001)
- **RedisInsight**: [http://localhost:5540](http://localhost:5540)

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
    Start only the support services (DB, Redis, RabbitMQ, MinIO) via Docker.

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

5. **Run Tasks Cleanup**:

    ```bash
    uv run -m scripts.tasks_cleanup
    ```

6. **Run Frontend**:

    ```bash
    uv run -m streamlit run src/frontend/app.py
    ```

## 📖 API Documentation

Once the application is running, you can access the interactive API documentation:

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### Key Endpoints

- `POST /api/v1/jobs`: Submit a new job.
- `GET /api/v1/jobs`: List all tasks with pagination and filtering.
- `GET /api/v1/jobs/{task_id}`: Get job status.
- `GET /api/v1/logs/{task_id}`: Retrieve execution logs.
- `GET /api/v1/metrics`: System and task metrics.
- `POST /api/v1/jobs/action/retry`: Replay all messages from DLQ.
- `POST /api/v1/jobs/action/{task_id}/retry`: Replay a specific message from DLQ.
- `GET /api/v1/health`: Health check.

## 📊 Monitoring & Dashboards

### Streamlit Dashboard

A real-time dashboard built with Streamlit for monitoring system health, task counts, and processing times.

- URL: `http://localhost:8501`

### HTMX Job Demo

A lightweight, reactive job list view demonstrating HTMX integration with FastAPI.

It uses Jinja2 templates ([src/api/templates/jobs.html](src/api/templates/jobs.html) and [src/api/templates/task_row.html](src/api/templates/task_row.html)) to provide real-time status updates and job submissions without full page reloads.

- URL: `http://localhost:8000/api/v1/jobs/view/demo`

## 🗄️ Database Migrations

The project uses **Alembic** for database migrations.

### Apply Migrations

```bash
# Apply all pending migrations to the latest version
alembic upgrade head
```

### Create a new Migration

```bash
# Generate a new migration based on model changes
alembic revision --autogenerate -m "Add new field to tasks"
```

### Rollback

```bash
# Rollback one migration
alembic downgrade -1
```

## 🧪 Testing & Benchmarking

### Unit Tests

```bash
# Run all tests
make test

# Run tests with verbose output
make test-verbose
```

### Load Testing (Locust)

Realistic load tests can be run using Locust.

```bash
# Run interactive Locust UI
./run-locust.sh interactive

# Run a quick smoke test
./run-locust.sh smoke

# Run a heavy stress test
./run-locust.sh stress
```

For more details, see [docs/load_test.md](docs/load_test.md).

## 🛠️ Maintenance & Utilities

- **Linting**: `make lint` (using Ruff)
- **Formatting**: `make format` (using Ruff)
- **Cleanup Cache**: `make clean-cache`
- **Kill Port**: `make kill-port PORT=8000` (MacOS only)
