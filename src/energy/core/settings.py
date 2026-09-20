"""Process-wide configuration loaded from the environment.

Layer: core

Settings are grouped into nested models (database, API, MinIO, Prefect, Dash,
logging, data) so that each layer depends only on the slice it needs. Values come
from environment variables first and from ``.env`` second; there are no hardcoded
credentials and no runtime mutation. Call :func:`get_settings` - it is cached, so
the environment is read once per process.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "ci", "staging", "production"]
LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
LogFormat = Literal["console", "json"]

_ENV_FILE = ".env"

_BASE_CONFIG = SettingsConfigDict(
    env_file=_ENV_FILE,
    env_file_encoding="utf-8",
    case_sensitive=False,
    extra="ignore",
)


class DatabaseSettings(BaseSettings):
    """PostgreSQL 16 + TimescaleDB connection and pooling configuration."""

    model_config = _BASE_CONFIG

    user: str = Field(default="energy", validation_alias="POSTGRES_USER")
    password: SecretStr = Field(default=SecretStr("energy"), validation_alias="POSTGRES_PASSWORD")
    name: str = Field(default="energy", validation_alias="POSTGRES_DB")
    host: str = Field(default="db", validation_alias="POSTGRES_HOST")
    port: int = Field(default=5432, ge=1, le=65535, validation_alias="POSTGRES_PORT")

    url_override: str | None = Field(default=None, validation_alias="DATABASE_URL")

    pool_size: int = Field(default=5, ge=1, validation_alias="DB_POOL_SIZE")
    max_overflow: int = Field(default=10, ge=0, validation_alias="DB_MAX_OVERFLOW")
    pool_timeout: int = Field(default=30, ge=1, validation_alias="DB_POOL_TIMEOUT")
    echo: bool = Field(default=False, validation_alias="DB_ECHO")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Return the SQLAlchemy DSN, preferring an explicit ``DATABASE_URL``.

        Returns:
            A ``postgresql+psycopg://`` DSN. When ``DATABASE_URL`` is set it wins,
            which is how CI and testcontainers point the application at an
            ephemeral database without rewriting the individual parts.
        """
        if self.url_override:
            return self.url_override
        dsn = PostgresDsn.build(
            scheme="postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            path=self.name,
        )
        return str(dsn)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safe_url(self) -> str:
        """Return the DSN with the password replaced, for logging and errors."""
        secret = self.password.get_secret_value()
        return self.url.replace(secret, "***") if secret else self.url


class APISettings(BaseSettings):
    """FastAPI service binding, CORS, and credential configuration."""

    model_config = _BASE_CONFIG

    host: str = Field(
        # Binding to all interfaces is correct inside a container: it is
        # reachable only on the internal compose network, and Caddy is the
        # single published ingress.
        default="0.0.0.0",  # noqa: S104  # nosec B104
        validation_alias="API_HOST",
    )
    port: int = Field(default=8000, ge=1, le=65535, validation_alias="API_PORT")
    workers: int = Field(default=2, ge=1, validation_alias="API_WORKERS")
    root_path: str = Field(default="/api", validation_alias="API_ROOT_PATH")
    cors_origins: str = Field(default="http://localhost", validation_alias="API_CORS_ORIGINS")
    secret_key: SecretStr = Field(
        default=SecretStr("change-me-generate-a-real-secret"),
        validation_alias="API_SECRET_KEY",
    )
    api_key_header: str = Field(default="X-API-Key", validation_alias="API_KEY_HEADER")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        """Split the comma-separated ``API_CORS_ORIGINS`` value into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


class DashSettings(BaseSettings):
    """Plotly Dash service binding."""

    model_config = _BASE_CONFIG

    host: str = Field(
        # Binding to all interfaces is correct inside a container: it is
        # reachable only on the internal compose network, and Caddy is the
        # single published ingress.
        default="0.0.0.0",  # noqa: S104  # nosec B104
        validation_alias="DASH_HOST",
    )
    port: int = Field(default=8050, ge=1, le=65535, validation_alias="DASH_PORT")
    url_base_pathname: str = Field(default="/", validation_alias="DASH_URL_BASE_PATHNAME")

    @field_validator("url_base_pathname")
    @classmethod
    def _normalise_base_pathname(cls, value: str) -> str:
        """Ensure the value is a URL path, not a filesystem path.

        Dash requires a prefix that both starts and ends with ``/``. On Windows,
        MSYS rewrites an exported ``/`` into the Git installation root (for
        example ``D:/git/Git/``) before it reaches the container, which Dash
        rejects at startup with an opaque configuration error. Rather than fail
        there, a value carrying a drive letter or a backslash is treated as
        mangled and replaced with the root prefix.

        Args:
            value: the configured prefix.

        Returns:
            A prefix guaranteed to start and end with ``/``.
        """
        candidate = value.strip()
        mangled = not candidate or "\\" in candidate or (len(candidate) > 1 and candidate[1] == ":")
        if mangled:
            return "/"
        if not candidate.startswith("/"):
            candidate = f"/{candidate}"
        if not candidate.endswith("/"):
            candidate = f"{candidate}/"
        return candidate


class MinioSettings(BaseSettings):
    """MinIO (S3-compatible) object storage configuration."""

    model_config = _BASE_CONFIG

    endpoint: str = Field(default="minio:9000", validation_alias="MINIO_ENDPOINT")
    access_key: str = Field(default="minioadmin", validation_alias="MINIO_ROOT_USER")
    secret_key: SecretStr = Field(
        default=SecretStr("minioadmin"), validation_alias="MINIO_ROOT_PASSWORD"
    )
    region: str = Field(default="us-east-1", validation_alias="MINIO_REGION")
    secure: bool = Field(default=False, validation_alias="MINIO_SECURE")
    bucket_bronze: str = Field(default="bronze-raw", validation_alias="MINIO_BUCKET_BRONZE")
    bucket_reports: str = Field(default="reports", validation_alias="MINIO_BUCKET_REPORTS")


class PrefectSettings(BaseSettings):
    """Prefect 3 orchestration server and worker configuration."""

    model_config = _BASE_CONFIG

    api_url: str = Field(
        default="http://prefect-server:4200/api", validation_alias="PREFECT_API_URL"
    )
    work_pool: str = Field(default="energy-pool", validation_alias="PREFECT_WORK_POOL")


class LoggingSettings(BaseSettings):
    """Structured logging configuration."""

    model_config = _BASE_CONFIG

    level: LogLevel = Field(default="INFO", validation_alias="LOG_LEVEL")
    format: LogFormat = Field(default="console", validation_alias="LOG_FORMAT")
    config_file: Path = Field(
        default=Path("config/logging.yaml"), validation_alias="LOG_CONFIG_FILE"
    )

    @field_validator("level", mode="before")
    @classmethod
    def _uppercase_level(cls, value: object) -> object:
        """Accept ``info`` as well as ``INFO``."""
        return value.upper() if isinstance(value, str) else value


class DataSettings(BaseSettings):
    """Canonical grid and data-freshness behaviour."""

    model_config = _BASE_CONFIG

    canonical_timezone: str = Field(default="UTC", validation_alias="CANONICAL_TIMEZONE")
    grid_frequency: str = Field(default="1h", validation_alias="CANONICAL_GRID_FREQUENCY")
    freshness_max_lag_hours: int = Field(
        default=6, ge=1, validation_alias="DATA_FRESHNESS_MAX_LAG_HOURS"
    )
    config_dir: Path = Field(default=Path("config"), validation_alias="CONFIG_DIR")

    @field_validator("canonical_timezone")
    @classmethod
    def _must_be_utc(cls, value: str) -> str:
        """Reject any canonical timezone other than UTC.

        Raises:
            ValueError: if a non-UTC timezone is configured. Silver and Gold are
                defined on a UTC grid; local time is a presentation concern only.
        """
        if value.upper() != "UTC":
            msg = f"canonical_timezone must be UTC, got {value!r}"
            raise ValueError(msg)
        return "UTC"


class Settings(BaseSettings):
    """Root configuration object composing every settings group."""

    model_config = _BASE_CONFIG

    app_name: str = Field(default="clarios-energy", validation_alias="APP_NAME")
    app_env: Environment = Field(default="local", validation_alias="APP_ENV")
    app_debug: bool = Field(default=False, validation_alias="APP_DEBUG")
    app_version: str = Field(default="0.1.0", validation_alias="APP_VERSION")

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    api: APISettings = Field(default_factory=APISettings)
    dash: DashSettings = Field(default_factory=DashSettings)
    minio: MinioSettings = Field(default_factory=MinioSettings)
    prefect: PrefectSettings = Field(default_factory=PrefectSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    data: DataSettings = Field(default_factory=DataSettings)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        """Return ``True`` when running in a deployed, non-local environment."""
        return self.app_env in ("staging", "production")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached, process-wide settings instance.

    Returns:
        The single :class:`Settings` object for this process. Cached so that the
        environment and ``.env`` file are read exactly once.

    Note:
        Tests that need different values should call ``get_settings.cache_clear()``
        after patching the environment, which is what the ``settings`` fixture in
        ``tests/conftest.py`` does.
    """
    return Settings()


__all__ = [
    "APISettings",
    "DashSettings",
    "DataSettings",
    "DatabaseSettings",
    "Environment",
    "LogFormat",
    "LogLevel",
    "LoggingSettings",
    "MinioSettings",
    "PrefectSettings",
    "Settings",
    "get_settings",
]
