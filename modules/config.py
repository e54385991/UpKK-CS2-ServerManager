"""Typed application settings loaded from the project-level ``.env`` file."""

from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote

from pydantic import model_validator
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
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    API_HOST: str
    API_PORT: int
    DEBUG: bool
    BACKEND_URL: str
    # None selects the safe environment default: enabled in development/test,
    # disabled in production. Operators must opt in explicitly in production.
    ALLOW_REGISTRATION: Optional[bool] = None

    # Logging Configuration
    # Options: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    LOG_LEVEL: str
    ASYNCSSH_LOG_LEVEL: str
    # Empty disables the unlisted /metrics endpoint.
    METRICS_BEARER_TOKEN: Optional[str] = None

    # Security
    SECRET_KEY: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int

    # Versioned encryption keys. JSON form is recommended, for example:
    # {"v1":"<32-byte-url-safe-base64-key>"}
    CREDENTIAL_ENCRYPTION_KEYS: str = ""
    CREDENTIAL_ACTIVE_KEY_ID: str = ""
    TOKEN_HASH_KEY: str = ""

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
        """Get an escaped MySQL database URL for the async driver."""
        return URL.create(
            "mysql+aiomysql",
            username=self.MYSQL_USER,
            password=self.MYSQL_PASSWORD,
            host=self.MYSQL_HOST,
            port=self.MYSQL_PORT,
            database=self.MYSQL_DATABASE,
        ).render_as_string(hide_password=False)

    @property
    def redis_url(self) -> str:
        """Get a Redis URL without allowing credentials to alter URI parsing."""
        if self.REDIS_PASSWORD:
            password = quote(self.REDIS_PASSWORD, safe="")
            return f"redis://:{password}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def registration_enabled(self) -> bool:
        if self.ALLOW_REGISTRATION is not None:
            return self.ALLOW_REGISTRATION
        return self.ENVIRONMENT != "production"

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.ENVIRONMENT != "production":
            return self
        if self.DEBUG:
            raise ValueError("DEBUG must be disabled in production")
        placeholders = ("change-this", "your-secret", "your_jwt")
        if any(marker in self.SECRET_KEY.lower() for marker in placeholders):
            raise ValueError("SECRET_KEY must be replaced in production")
        if any(marker in self.JWT_SECRET_KEY.lower() for marker in placeholders):
            raise ValueError("JWT_SECRET_KEY must be replaced in production")
        if not self.CREDENTIAL_ENCRYPTION_KEYS or not self.CREDENTIAL_ACTIVE_KEY_ID:
            raise ValueError("Credential encryption keys are required in production")
        try:
            from cs2_manager.infrastructure.credentials import CredentialCipher

            CredentialCipher.from_settings(self)
        except ValueError as exc:
            raise ValueError(f"Invalid credential encryption keyring: {exc}") from exc
        if len(self.TOKEN_HASH_KEY) < 32:
            raise ValueError("TOKEN_HASH_KEY must contain at least 32 characters in production")
        if self.METRICS_BEARER_TOKEN and len(self.METRICS_BEARER_TOKEN) < 32:
            raise ValueError(
                "METRICS_BEARER_TOKEN must contain at least 32 characters when enabled"
            )
        return self


# Global settings instance
settings = Settings()
