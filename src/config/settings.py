from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from pydantic import SecretStr
from pydantic.functional_validators import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.schemas.types import EnvironmentEnum


class BaseSettingsConfig(BaseSettings):
    """Base configuration class for settings.

    This class extends BaseSettings to provide common configuration options
    for environment variable loading and processing.

    Attributes
    ----------
    model_config : SettingsConfigDict
        Configuration dictionary for the settings model specifying env file location,
        encoding and other processing options.
    """

    model_config = SettingsConfigDict(
        env_file=str(Path(".env").absolute()),
        env_file_encoding="utf-8",
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class Settings(BaseSettingsConfig):
    """Application settings class containing database and other credentials."""

    # ===== API SERVER =====
    ENVIRONMENT: EnvironmentEnum = EnvironmentEnum.DEVELOPMENT
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 1
    RELOAD: bool = False
    DEBUG: bool = False

    # ===== RABBITMQ =====
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: SecretStr = SecretStr("guest")
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_VHOST: str = "/"

    # ===== DATABASE =====
    POSTGRES_USER: str = "taskflow"
    POSTGRES_PASSWORD: SecretStr = SecretStr("taskflow")
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "taskflow_db"
    API_DB_NAME: str = "users_db"

    # ===== REDIS CACHE =====
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: SecretStr = SecretStr("your_redis_password")
    REDIS_DB: int = 0

    @field_validator("PORT", "POSTGRES_PORT", "REDIS_PORT", mode="before")
    @classmethod
    def parse_port_fields(cls, v: str | int) -> int:
        """Parses port fields to ensure they are integers."""
        if isinstance(v, str):
            try:
                return int(v.strip())
            except ValueError:
                raise ValueError(f"Invalid port value: {v}") from None

        if isinstance(v, int) and not (1 <= v <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")

        return v

    @property
    def database_url(self) -> str:
        """
        Constructs the database connection URL.

        Returns
        -------
        str
            Complete database connection URL in the format:
            postgresql+asyncpg://user:password@host:port/dbname
        """
        password: str = quote(self.POSTGRES_PASSWORD.get_secret_value(), safe="")
        url: str = (
            f"postgresql+asyncpg://{self.POSTGRES_USER}"
            f":{password}"
            f"@{self.POSTGRES_HOST}"
            f":{self.POSTGRES_PORT}"
            f"/{self.POSTGRES_DB}"
        )
        return url

    @property
    def database_url2(self) -> str:
        """
        Constructs the API database connection URL.

        Returns
        -------
        str
            Complete database connection URL in the format:
            postgresql+asyncpg://user:password@host:port/dbname
        """
        password: str = quote(self.POSTGRES_PASSWORD.get_secret_value(), safe="")
        url: str = (
            f"postgresql+asyncpg://{self.POSTGRES_USER}"
            f":{password}"
            f"@{self.POSTGRES_HOST}"
            f":{self.POSTGRES_PORT}"
            f"/{self.API_DB_NAME}"
        )
        return url

    @property
    def rabbitmq_url(self) -> str:
        """Constructs the RabbitMQ connection URL.

        Returns
        -------
        str
            Complete RabbitMQ connection URL in the format:
            amqp://user:password@host:port/vhost
        """
        password: str = quote(self.RABBITMQ_PASSWORD.get_secret_value(), safe="")
        url: str = (
            f"amqp://{self.RABBITMQ_USER}"
            f":{password}"
            f"@{self.RABBITMQ_HOST}"
            f":{self.RABBITMQ_PORT}"
            f"/{self.RABBITMQ_VHOST}"
        )
        return url

    @property
    def redis_url(self) -> str:
        """
        Constructs the Redis connection URL.

        Returns
        -------
        str
            Complete Redis connection URL in the format:
            redis://[:password@]host:port/db
        """
        raw_password = self.REDIS_PASSWORD.get_secret_value()
        if raw_password:
            password = quote(raw_password, safe="")
            url: str = f"redis://:{password}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        else:
            url = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return url


def refresh_settings() -> Settings:
    """Refresh environment variables and return new Settings instance.

    This function reloads environment variables from .env file and creates
    a new Settings instance with the updated values.

    Returns
    -------
    Settings
        A new Settings instance with refreshed environment variables
    """
    load_dotenv(override=True)
    return Settings()


def setup_env() -> None:
    """Sets environment variables for Together AI and OpenRouter clients."""
    pass


app_settings: Settings = refresh_settings()

# Call setup_env only once at startup
_setup_env_called: bool = False


def setup_env_once() -> None:
    """Sets environment variables for Together AI and OpenRouter clients. Called only once."""
    global _setup_env_called
    if not _setup_env_called:
        setup_env()
        _setup_env_called = True


# Automatically call setup_env when the module is imported
setup_env_once()
