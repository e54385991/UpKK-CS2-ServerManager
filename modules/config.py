"""Typed application settings loaded from the project-level ``.env`` file."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # PostgreSQL 18+ configuration
    POSTGRES_HOST: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DATABASE: str

    # SQLAlchemy connection pool and migration configuration
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 3600
    DB_POOL_PRE_PING: bool = True
    DB_ECHO: bool = False
    DB_MIGRATION_LOCK_TIMEOUT_SECONDS: int = 300

    # Redis Configuration
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_PASSWORD: str | None = None
    REDIS_DB: int
    # Namespace Redis keys so two panels can share one Redis DB (operators
    # often leave REDIS_DB=0). Empty keeps the historical unprefixed keys.
    REDIS_KEY_PREFIX: str = ""
    # Browser cookies are not port-scoped. Suffix isolates
    # upkk_access_token_<port> when two consoles share a host (3000 vs 3001).
    SESSION_COOKIE_SUFFIX: str = ""

    # Redis Connection Pool Configuration
    # These settings optimize Redis connection management for better performance
    REDIS_POOL_SIZE: int
    REDIS_HEALTH_CHECK_INTERVAL: int
    REDIS_SOCKET_CONNECT_TIMEOUT: int
    REDIS_SOCKET_TIMEOUT: int

    # Application Configuration
    API_HOST: str
    API_PORT: int
    # Production is the safe default; development diagnostics must be opted in.
    DEBUG: bool = False
    RUN_MODE: str = "production"
    BACKEND_URL: str
    # Public Next.js origin. FastAPI leftover HTML pages 307 here by default.
    # In the Caddy three-service topology this is the public gateway origin.
    CONSOLE_PUBLIC_URL: str = "http://127.0.0.1:3000"
    # redirect | serve | gone — default sends the old Jinja console to Next.
    LEGACY_HTML_CONSOLE: str = "redirect"

    # Logging Configuration
    # Options: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    LOG_LEVEL: str
    ASYNCSSH_LOG_LEVEL: str

    # Security
    SECRET_KEY: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int

    # Optional Fernet key used only for AI provider credentials.  The
    # application can start without it, but AI provider settings cannot be
    # enabled or persisted until a valid key is configured.
    AI_CREDENTIAL_ENCRYPTION_KEY: str | None = None

    # SSH Authentication Configuration
    # Options: "password", "key", "both"
    # "password" - Only password authentication allowed
    # "key" - Only SSH key authentication allowed
    # "both" - Both password and key authentication allowed
    SSH_AUTH_MODE: str

    # Google OAuth Configuration
    GOOGLE_CLIENT_ID: str | None = None
    # Google CallbackURL = https://your-domain.com/google-callback

    @property
    def database_url(self) -> URL:
        """Return a safely encoded Psycopg 3 SQLAlchemy URL."""
        return URL.create(
            "postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DATABASE,
        )

    @property
    def redis_url(self) -> str:
        """Get Redis connection URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @field_validator("POSTGRES_PORT", "REDIS_PORT", "API_PORT")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator(
        "DB_POOL_SIZE",
        "DB_POOL_TIMEOUT",
        "DB_POOL_RECYCLE",
        "DB_MIGRATION_LOCK_TIMEOUT_SECONDS",
        "REDIS_POOL_SIZE",
        "REDIS_HEALTH_CHECK_INTERVAL",
        "REDIS_SOCKET_CONNECT_TIMEOUT",
        "REDIS_SOCKET_TIMEOUT",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    @classmethod
    def validate_positive_setting(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("setting must be greater than zero")
        return value

    @field_validator("DB_MAX_OVERFLOW")
    @classmethod
    def validate_max_overflow(cls, value: int) -> int:
        if value < 0:
            raise ValueError("DB_MAX_OVERFLOW must be zero or greater")
        return value

    @field_validator("REDIS_DB")
    @classmethod
    def validate_redis_db(cls, value: int) -> int:
        if value < 0:
            raise ValueError("REDIS_DB must be zero or greater")
        return value

    @field_validator("LOG_LEVEL", "ASYNCSSH_LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized

    @field_validator("SSH_AUTH_MODE")
    @classmethod
    def validate_ssh_auth_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"password", "key", "both"}:
            raise ValueError("SSH_AUTH_MODE must be password, key, or both")
        return normalized

    @field_validator("LEGACY_HTML_CONSOLE")
    @classmethod
    def validate_legacy_console_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"redirect", "serve", "gone"}:
            raise ValueError("LEGACY_HTML_CONSOLE must be redirect, serve, or gone")
        return normalized

    @field_validator("RUN_MODE")
    @classmethod
    def validate_run_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"development", "production", "test"}:
            raise ValueError("RUN_MODE must be development, production, or test")
        return normalized

    @field_validator("BACKEND_URL", "CONSOLE_PUBLIC_URL")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must include an http or https scheme and host")
        return value.strip().rstrip("/")

    @field_validator("SECRET_KEY", "JWT_SECRET_KEY")
    @classmethod
    def validate_secret_strength(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 32:
            raise ValueError("secret keys must contain at least 32 characters")
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate process settings exactly once.

    Keeping construction behind a cache makes startup deterministic while
    allowing tests to clear the cache or override the FastAPI dependency.
    ``settings`` below remains a compatibility export for existing imports.
    """

    return Settings()


# Compatibility export. New request handlers should inject ``get_settings``
# through ``SettingsDependency`` instead of importing this module global.
settings = get_settings()
