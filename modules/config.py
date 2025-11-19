"""
Configuration module for CS2 Server Manager
Handles Redis, MySQL connections and other basic settings
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
    
    # Redis Configuration
    REDIS_HOST: str = "1Panel-redis-oAZc"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = "redis_rYpBai"
    REDIS_DB: int = 0
    
    # Application Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    DEBUG: bool = True
    BACKEND_URL: str = "http://localhost:8000"  # Backend URL for server status reporting
    
    # Security
    SECRET_KEY: str = "your-secret-d52vu7LqPwdn4drq"
    JWT_SECRET_KEY: str = "your-jwt-secret-key-change-d52vu7LqPwdn4drq"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 1 week (7 days * 24 hours * 60 minutes)
    
    # SSH Authentication Configuration
    # Options: "password", "key", "both"
    # "password" - Only password authentication allowed
    # "key" - Only SSH key authentication allowed
    # "both" - Both password and key authentication allowed
    SSH_AUTH_MODE: str = "password"  # Default to password only
    
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
