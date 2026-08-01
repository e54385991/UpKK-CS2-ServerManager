"""Typed application settings loaded from the project-level ``.env`` file."""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # MySQL Configuration
    MYSQL_HOST: str
    MYSQL_PORT: int
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    MYSQL_DATABASE: str

    # MySQL Connection Pool Configuration
    # These settings optimize database connection management for better performance
    MYSQL_POOL_SIZE: int
    MYSQL_MAX_OVERFLOW: int
    MYSQL_POOL_TIMEOUT: int
    MYSQL_POOL_RECYCLE: int
    MYSQL_POOL_PRE_PING: bool
    MYSQL_ECHO: bool

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
    def mysql_url(self) -> str:
        """Get MySQL database URL for async"""
        return f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"

    @property
    def redis_url(self) -> str:
        """Get Redis connection URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


# Global settings instance
settings = Settings()
