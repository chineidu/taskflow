from dataclasses import dataclass, field
from pathlib import Path

from omegaconf import DictConfig, OmegaConf
from pydantic import BaseModel, Field

from src import ROOT


@dataclass(slots=True, kw_only=True)
class RabbitMQConfig:
    max_retries: int = field(default=3, metadata={"description": "Maximum number of connection retries"})
    retry_delay: int = field(
        default=1, metadata={"description": "Delay between connection retries in seconds"}
    )
    connection_timeout: int = field(default=5, metadata={"description": "Connection timeout in seconds"})
    heartbeat: int = field(default=60, metadata={"description": "Heartbeat interval in seconds"})
    prefetch_count: int = field(default=5, metadata={"description": "Number of messages to prefetch"})


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


class AppConfig(BaseModel):
    """Application configuration with validation."""

    rabbitmq_config: RabbitMQConfig = Field(
        default_factory=RabbitMQConfig, description="Configuration settings for RabbitMQ connection"
    )
    api_config: APIConfig = Field(description="Configuration settings for the API"
    )


config_path: Path = ROOT / "config/config.yaml"
config: DictConfig = OmegaConf.load(config_path).config
resolved_cfg = OmegaConf.to_container(config, resolve=True)
app_config: AppConfig = AppConfig(**dict(resolved_cfg))  # type: ignore
