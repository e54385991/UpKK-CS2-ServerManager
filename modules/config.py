"""Typed application settings loaded from the project-level ``.env`` file."""

from pathlib import Path
from typing import Optional

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
    REDIS_PASSWORD: Optional[str]
    REDIS_DB: int

    # Redis Connection Pool Configuration
    # These settings optimize Redis connection management for better performance
    REDIS_POOL_SIZE: int
    REDIS_HEALTH_CHECK_INTERVAL: int
    REDIS_SOCKET_CONNECT_TIMEOUT: int
    REDIS_SOCKET_TIMEOUT: int

    # Application Configuration
    API_HOST: str
    API_PORT: int
    DEBUG: bool
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
    AI_CREDENTIAL_ENCRYPTION_KEY: Optional[str] = None

    # SSH Authentication Configuration
    # Options: "password", "key", "both"
    # "password" - Only password authentication allowed
    # "key" - Only SSH key authentication allowed
    # "both" - Both password and key authentication allowed
    SSH_AUTH_MODE: str

    # Google OAuth Configuration
    GOOGLE_CLIENT_ID: Optional[str]
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


# Global settings instance
settings = Settings()
