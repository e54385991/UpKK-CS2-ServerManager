"""
Database models for CS2 Server Manager
Using SQLModel for seamless FastAPI integration
"""
from sqlmodel import SQLModel, Field, Column, select
from sqlalchemy import Text, Enum as SQLEnum, Integer, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from datetime import datetime
import enum


class AuthType(str, enum.Enum):
    """SSH Authentication types"""
    PASSWORD = "password"
    KEY_FILE = "key_file"


class ServerStatus(str, enum.Enum):
    """Server status enumeration"""
    PENDING = "pending"
    DEPLOYING = "deploying"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


# SQLModel uses SQLModel as base instead of declarative_base()
# The 'table=True' parameter indicates this is a database table model


class User(SQLModel, table=True):
    """User model for authentication and authorization"""
    __tablename__ = "users"
    
    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    username: str = Field(max_length=100, unique=True, nullable=False, index=True)
    email: str = Field(max_length=255, unique=True, nullable=False, index=True)
    hashed_password: str = Field(max_length=255, nullable=False)
    is_active: bool = Field(default=True)
    is_admin: bool = Field(default=False)
    api_key: Optional[str] = Field(default=None, max_length=64, unique=True, index=True)
    steam_api_key: Optional[str] = Field(default=None, max_length=64)
    created_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP"})
    updated_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP", "onupdate": func.now()})
    
    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', email='{self.email}')>"
    
    @property
    def has_api_key(self) -> bool:
        """Check if user has an API key configured"""
        return self.api_key is not None
    
    @property
    def has_steam_api_key(self) -> bool:
        """Check if user has a Steam API key configured"""
        return self.steam_api_key is not None
    
    @classmethod
    async def get_by_username(cls, session: AsyncSession, username: str) -> Optional["User"]:
        """Get user by username"""
        result = await session.execute(select(cls).where(cls.username == username))
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_by_email(cls, session: AsyncSession, email: str) -> Optional["User"]:
        """Get user by email"""
        result = await session.execute(select(cls).where(cls.email == email))
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_by_api_key(cls, session: AsyncSession, api_key: str) -> Optional["User"]:
        """Get user by API key"""
        result = await session.execute(select(cls).where(cls.api_key == api_key))
        return result.scalar_one_or_none()


class Server(SQLModel, table=True):
    """CS2 Server model"""
    __tablename__ = "servers"
    
    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    name: str = Field(max_length=255, nullable=False, index=True)
    host: str = Field(max_length=255, nullable=False)
    ssh_port: int = Field(default=22)
    ssh_user: str = Field(max_length=100, nullable=False)
    auth_type: AuthType = Field(sa_column=Column(SQLEnum(AuthType), nullable=False))
    ssh_password: Optional[str] = Field(default=None, max_length=255)
    ssh_key_path: Optional[str] = Field(default=None, max_length=500)
    sudo_password: Optional[str] = Field(default=None, max_length=255)
    
    # Server configuration
    game_port: int = Field(default=27015)
    game_directory: str = Field(default="/home/cs2server/cs2", max_length=500)
    status: ServerStatus = Field(default=ServerStatus.PENDING, sa_column=Column(SQLEnum(ServerStatus), default=ServerStatus.PENDING))
    
    # LGSM-style server start parameters
    server_name: str = Field(default="CS2 Server", max_length=255)
    server_password: Optional[str] = Field(default=None, max_length=255)
    rcon_password: Optional[str] = Field(default=None, max_length=255)
    steam_account_token: Optional[str] = Field(default=None, max_length=255)
    default_map: str = Field(default="de_dust2", max_length=100)
    max_players: int = Field(default=32)
    tickrate: int = Field(default=128)
    game_mode: str = Field(default="competitive", max_length=50)
    game_type: str = Field(default="0", max_length=50)
    
    # Advanced start parameters
    additional_parameters: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    ip_address: Optional[str] = Field(default=None, max_length=100)
    client_port: Optional[int] = Field(default=None)
    tv_port: Optional[int] = Field(default=None)
    tv_enable: bool = Field(default=False)
    
    # Server-to-backend communication
    api_key: Optional[str] = Field(default=None, max_length=64, unique=True, index=True)
    backend_url: Optional[str] = Field(default=None, max_length=500)
    
    # Auto-cleanup configuration
    auto_clear_crash_hours: Optional[int] = Field(default=None)
    last_status_check: Optional[datetime] = Field(default=None)
    
    # Web-based monitoring configuration
    enable_panel_monitoring: bool = Field(default=False)
    monitor_interval_seconds: int = Field(default=60)
    auto_restart_on_crash: bool = Field(default=True)
    
    # A2S query configuration for server monitoring
    a2s_query_host: Optional[str] = Field(default=None, max_length=255)
    a2s_query_port: Optional[int] = Field(default=None)
    enable_a2s_monitoring: bool = Field(default=False)
    a2s_failure_threshold: int = Field(default=3)
    a2s_check_interval_seconds: int = Field(default=60)
    
    # Auto-update configuration
    current_game_version: Optional[str] = Field(default=None, max_length=50)
    enable_auto_update: bool = Field(default=True)
    update_check_interval_hours: int = Field(default=1)
    last_update_check: Optional[datetime] = Field(default=None)
    last_update_time: Optional[datetime] = Field(default=None)
    
    # CPU affinity configuration
    cpu_affinity: Optional[str] = Field(default=None, max_length=500)
    
    # Additional info
    description: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    last_deployed: Optional[datetime] = Field(default=None)
    created_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP"})
    updated_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP", "onupdate": func.now()})
    
    def __repr__(self):
        status_val = self.status.value if self.status else "unknown"
        return f"<Server(id={self.id}, name='{self.name}', host='{self.host}', status='{status_val}')>"
    
    @property
    def is_password_auth(self) -> bool:
        """Check if server uses password authentication"""
        return self.auth_type == AuthType.PASSWORD
    
    @property
    def is_key_auth(self) -> bool:
        """Check if server uses key file authentication"""
        return self.auth_type == AuthType.KEY_FILE
    
    @property
    def is_running(self) -> bool:
        """Check if server is running"""
        return self.status == ServerStatus.RUNNING
    
    @property
    def is_stopped(self) -> bool:
        """Check if server is stopped"""
        return self.status == ServerStatus.STOPPED
    
    @property
    def is_deploying(self) -> bool:
        """Check if server is being deployed"""
        return self.status == ServerStatus.DEPLOYING
    
    @property
    def is_error(self) -> bool:
        """Check if server is in error state"""
        return self.status == ServerStatus.ERROR
    
    def set_status(self, status: ServerStatus) -> None:
        """Set server status - convenience method for cleaner code"""
        self.status = status
    
    @classmethod
    async def get_by_id_and_user(cls, session: AsyncSession, server_id: int, user_id: int) -> Optional["Server"]:
        """Get server by ID and user ID - common pattern in this application"""
        result = await session.execute(
            select(cls).where(cls.id == server_id, cls.user_id == user_id)
        )
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_by_name_and_user(cls, session: AsyncSession, name: str, user_id: int) -> Optional["Server"]:
        """Check if server name exists for a user"""
        result = await session.execute(
            select(cls).where(cls.name == name, cls.user_id == user_id)
        )
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_by_host_directory_and_user(
        cls, session: AsyncSession, host: str, game_directory: str, user_id: int
    ) -> Optional["Server"]:
        """Check if server with same host and directory exists for a user"""
        result = await session.execute(
            select(cls).where(
                cls.host == host,
                cls.game_directory == game_directory,
                cls.user_id == user_id
            )
        )
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_all_by_user(cls, session: AsyncSession, user_id: int, skip: int = 0, limit: int = 100) -> List["Server"]:
        """Get all servers for a user with pagination"""
        result = await session.execute(
            select(cls).where(cls.user_id == user_id).offset(skip).limit(limit)
        )
        return result.scalars().all()
    
    @classmethod
    async def get_by_api_key(cls, session: AsyncSession, api_key: str) -> Optional["Server"]:
        """Get server by API key"""
        result = await session.execute(select(cls).where(cls.api_key == api_key))
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_all_with_panel_monitoring(cls, session: AsyncSession) -> List["Server"]:
        """Get all servers with panel monitoring enabled"""
        result = await session.execute(
            select(cls).where(cls.enable_panel_monitoring.is_(True))
        )
        return result.scalars().all()
    
    @classmethod
    async def get_all_with_auto_update(cls, session: AsyncSession) -> List["Server"]:
        """Get all servers with auto-update enabled"""
        result = await session.execute(
            select(cls).where(cls.enable_auto_update.is_(True))
        )
        return result.scalars().all()


class DeploymentLog(SQLModel, table=True):
    """Deployment log model"""
    __tablename__ = "deployment_logs"
    
    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    server_id: int = Field(nullable=False, index=True)
    action: str = Field(max_length=50, nullable=False)
    status: str = Field(max_length=50, nullable=False)
    output: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP"})
    
    def __repr__(self):
        return f"<DeploymentLog(id={self.id}, server_id={self.server_id}, action='{self.action}', status='{self.status}')>"
    
    @classmethod
    async def get_logs_by_server(cls, session: AsyncSession, server_id: int, skip: int = 0, limit: int = 50) -> List["DeploymentLog"]:
        """Get deployment logs for a server with pagination"""
        result = await session.execute(
            select(cls)
            .where(cls.server_id == server_id)
            .order_by(cls.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()


class MonitoringLog(SQLModel, table=True):
    """Panel monitoring log model - stores monitoring events and auto-restart activities"""
    __tablename__ = "monitoring_logs"
    
    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    server_id: int = Field(nullable=False, index=True)
    event_type: str = Field(max_length=50, nullable=False)
    status: str = Field(max_length=50, nullable=False)
    message: str = Field(sa_column=Column(Text, nullable=False))
    created_at: Optional[datetime] = Field(default=None, index=True, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP"})
    
    def __repr__(self):
        return f"<MonitoringLog(id={self.id}, server_id={self.server_id}, event_type='{self.event_type}', status='{self.status}')>"


class ScheduledTask(SQLModel, table=True):
    """Scheduled task model for automated server operations"""
    __tablename__ = "scheduled_tasks"
    
    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    server_id: int = Field(
        sa_column=Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    name: str = Field(max_length=255, nullable=False)
    action: str = Field(max_length=50, nullable=False)
    enabled: bool = Field(default=True)
    
    # Schedule configuration
    schedule_type: str = Field(max_length=50, nullable=False)
    schedule_value: str = Field(max_length=255, nullable=False)
    
    # Execution tracking
    last_run: Optional[datetime] = Field(default=None)
    next_run: Optional[datetime] = Field(default=None)
    run_count: int = Field(default=0)
    last_status: Optional[str] = Field(default=None, max_length=50)
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    
    created_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP"})
    updated_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP", "onupdate": func.now()})
    
    def __repr__(self):
        return f"<ScheduledTask(id={self.id}, server_id={self.server_id}, name='{self.name}', action='{self.action}', enabled={self.enabled})>"
    
    @property
    def is_enabled(self) -> bool:
        """Check if task is enabled - property for consistency"""
        return self.enabled
    
    @property
    def has_run(self) -> bool:
        """Check if task has been executed at least once"""
        return self.run_count > 0
    
    @property
    def last_run_failed(self) -> bool:
        """Check if last run failed"""
        return self.last_status == "failed" if self.last_status else False
    
    @classmethod
    async def get_by_id_and_server(cls, session: AsyncSession, task_id: int, server_id: int) -> Optional["ScheduledTask"]:
        """Get scheduled task by ID and server ID"""
        result = await session.execute(
            select(cls).where(cls.id == task_id, cls.server_id == server_id)
        )
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_all_by_server(cls, session: AsyncSession, server_id: int) -> List["ScheduledTask"]:
        """Get all scheduled tasks for a server"""
        result = await session.execute(
            select(cls).where(cls.server_id == server_id).order_by(cls.id.desc())
        )
        return result.scalars().all()


class InitializedServer(SQLModel, table=True):
    """Initialized server configuration from setup wizard"""
    __tablename__ = "initialized_servers"
    
    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    name: str = Field(max_length=255, nullable=False)
    host: str = Field(max_length=255, nullable=False)
    ssh_port: int = Field(default=22)
    ssh_user: str = Field(max_length=100, nullable=False)
    ssh_password: str = Field(max_length=255, nullable=False)
    game_directory: str = Field(default="/home/cs2server/cs2", max_length=500)
    created_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP"})
    updated_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP", "onupdate": func.now()})
    
    def __repr__(self):
        return f"<InitializedServer(id={self.id}, user_id={self.user_id}, name='{self.name}', host='{self.host}')>"
    
    @classmethod
    async def get_all_by_user(cls, session: AsyncSession, user_id: int) -> List["InitializedServer"]:
        """Get all initialized servers for a user"""
        result = await session.execute(
            select(cls).where(cls.user_id == user_id).order_by(cls.created_at.desc())
        )
        return result.scalars().all()


# For backward compatibility with existing code that uses Base.metadata
# SQLModel uses SQLModel.metadata instead
Base = SQLModel
