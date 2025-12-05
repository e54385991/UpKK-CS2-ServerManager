"""
Configuration module for CS2 Server Manager
Handles Redis, MySQL connections and other basic settings
Connection pool configurations are used by SQLAlchemy engine (which powers SQLModel)
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # MySQL Configuration
    MYSQL_HOST: str = "1Panel-mysql-KZBC"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "cs2_manager"
    MYSQL_PASSWORD: str = "7cfJBzXHcja5TeiS"
    MYSQL_DATABASE: str = "cs2_manager"
    
    # MySQL Connection Pool Configuration
    # These settings optimize database connection management for better performance
    MYSQL_POOL_SIZE: int = 5  # Number of connections to keep open in the pool
    MYSQL_MAX_OVERFLOW: int = 10  # Maximum overflow connections when pool is full
    MYSQL_POOL_TIMEOUT: int = 30  # Seconds to wait for a connection from the pool
    MYSQL_POOL_RECYCLE: int = 3600  # Seconds before a connection is recycled (1 hour)
    MYSQL_POOL_PRE_PING: bool = True  # Enable connection health check before use
    MYSQL_ECHO: bool = False  # Enable/disable SQLAlchemy SQL query logging (sqlalchemy.engine.Engine)
    
    # Redis Configuration
    REDIS_HOST: str = "1Panel-redis-oAZc"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = "redis_rYpBai"
    REDIS_DB: int = 0
    
    # Redis Connection Pool Configuration
    # These settings optimize Redis connection management for better performance
    REDIS_POOL_SIZE: int = 10  # Maximum number of connections in the pool
    REDIS_RETRY_ON_TIMEOUT: bool = True  # Retry operation on timeout
    REDIS_HEALTH_CHECK_INTERVAL: int = 30  # Seconds between health checks
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5  # Seconds for socket connection timeout
    REDIS_SOCKET_TIMEOUT: int = 5  # Seconds for socket read/write timeout
    
    # Application Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    DEBUG: bool = True
    BACKEND_URL: str = "http://localhost:8000"  # Backend URL for server status reporting
    
    # Logging Configuration
    # Options: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    LOG_LEVEL: str = "INFO"  # General application logging level
    ASYNCSSH_LOG_LEVEL: str = "WARNING"  # AsyncSSH library logging level (reduce verbosity)
    
    # Security
    SECRET_KEY: str = "your-secret-key-change-this-in-production"
    JWT_SECRET_KEY: str = "your-jwt-secret-key-change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 1 week (7 days * 24 hours * 60 minutes)
    
    # SSH Authentication Configuration
    # Options: "password", "key", "both"
    # "password" - Only password authentication allowed
    # "key" - Only SSH key authentication allowed
    # "both" - Both password and key authentication allowed
    SSH_AUTH_MODE: str = "password"  # Default to password only
    
    # Google OAuth Configuration
    GOOGLE_CLIENT_ID: Optional[str] = None  # Google OAuth Client ID from Google Cloud Console
    # Google CallbackURL = https://your-domain.com/google-callback
    
    class Config:
        env_file = ".env"
        case_sensitive = True
    
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
