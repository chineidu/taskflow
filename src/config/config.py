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


class AppConfig(BaseModel):
    """Application configuration with validation."""

    rabbitmq_config: RabbitMQConfig = Field(
        default_factory=RabbitMQConfig, description="Configuration settings for RabbitMQ connection"
    )


config_path: Path = ROOT / "config/config.yaml"
config: DictConfig = OmegaConf.load(config_path).config
resolved_cfg = OmegaConf.to_container(config, resolve=True)
app_config: AppConfig = AppConfig(**dict(resolved_cfg))  # type: ignore
