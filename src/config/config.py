from dataclasses import dataclass, field
from pathlib import Path

from omegaconf import DictConfig, OmegaConf
from pydantic import BaseModel, Field

from src import ROOT


@dataclass(slots=True, kw_only=True)
class QueueNames:
    task_queue: str = field(metadata={"description": "Name of the task queue"})
    result_queue: str = field(metadata={"description": "Name of the result queue"})


@dataclass(slots=True, kw_only=True)
class DLQConfig:
    dlq_name: str = field(metadata={"description": "Name of the dead-letter queue"})
    dlx_name: str = field(metadata={"description": "Name of the dead-letter exchange"})


@dataclass(slots=True, kw_only=True)
class RabbitMQConfig:
    max_retries: int = field(default=3, metadata={"description": "Maximum number of connection retries"})
    retry_delay: int = field(
        default=1,
        metadata={"description": "Delay between connection retries in seconds"},
    )
    connection_timeout: int = field(default=5, metadata={"description": "Connection timeout in seconds"})
    heartbeat: int = field(default=60, metadata={"description": "Heartbeat interval in seconds"})
    prefetch_count: int = field(default=5, metadata={"description": "Number of messages to prefetch"})
    queue_names: QueueNames = field(metadata={"description": "Names of the RabbitMQ queues"})
    dlq_config: DLQConfig = field(metadata={"description": "Dead-letter queue configuration"})
    message_retries: int = field(default=3, metadata={"description": "Number of times to retry a message"})
    retry_backoff_delay: int = field(
        default=2, metadata={"description": "Backoff delay multiplier for message retries"}
    )


@dataclass(slots=True, kw_only=True)
class CORS:
    """CORS configuration class."""

    allow_origins: list[str] = field(
        default_factory=list, metadata={"description": "Allowed origins for CORS."}
    )
    allow_credentials: bool = field(metadata={"description": "Allow credentials for CORS."})
    allow_methods: list[str] = field(
        default_factory=list, metadata={"description": "Allowed methods for CORS."}
    )
    allow_headers: list[str] = field(
        default_factory=list, metadata={"description": "Allowed headers for CORS."}
    )


@dataclass(slots=True, kw_only=True)
class Middleware:
    """Middleware configuration class."""

    cors: CORS = field(metadata={"description": "CORS configuration."})


@dataclass(slots=True, kw_only=True)
class Ratelimit:
    """Ratelimit configuration class."""

    default_rate: int = field(metadata={"description": "Default rate limit (e.g., 50)."})
    burst_rate: int = field(metadata={"description": "Burst rate limit (e.g., 100)."})
    login_rate: int = field(metadata={"description": "Login rate limit (e.g., 10)."})


@dataclass(slots=True, kw_only=True)
class APIConfig:
    """API-level configuration."""

    title: str = field(metadata={"description": "The title of the API."})
    name: str = field(metadata={"description": "The name of the API."})
    description: str = field(metadata={"description": "The description of the API."})
    version: str = field(metadata={"description": "The version of the API."})
    status: str = field(metadata={"description": "The current status of the API."})
    prefix: str = field(metadata={"description": "The prefix for the API routes."})
    auth_prefix: str = field(metadata={"description": "The prefix for the authentication routes."})
    middleware: Middleware = field(metadata={"description": "Middleware configuration."})
    ratelimit: Ratelimit = field(metadata={"description": "Ratelimit configuration."})


@dataclass(slots=True, kw_only=True)
class DatabaseConfig:
    """Database configuration class."""

    pool_size: int = field(default=30, metadata={"description": "Number of connections to keep in pool"})
    max_overflow: int = field(default=10, metadata={"description": "Number of extra connections allowed"})
    pool_timeout: int = field(default=20, metadata={"description": "Seconds to wait for a connection"})
    pool_recycle: int = field(
        default=1800,
        metadata={"description": "Seconds after which to recycle connections"},
    )
    pool_pre_ping: bool = field(
        default=True, metadata={"description": "Whether to test connections before use"}
    )
    expire_on_commit: bool = field(
        default=False, metadata={"description": "Whether to expire objects on commit"}
    )


class AppConfig(BaseModel):
    """Application configuration with validation."""

    rabbitmq_config: RabbitMQConfig = Field(
        description="Configuration settings for RabbitMQ connection",
    )
    api_config: APIConfig = Field(description="Configuration settings for the API")
    database_config: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
        description="Configuration settings for the Database connection",
    )


config_path: Path = ROOT / "config/config.yaml"
config: DictConfig = OmegaConf.load(config_path).config
resolved_cfg = OmegaConf.to_container(config, resolve=True)
app_config: AppConfig = AppConfig(**dict(resolved_cfg))  # type: ignore
