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
    
    # GitHub proxy configuration
    github_proxy: Optional[str] = Field(default=None, max_length=500)
    
    # Panel proxy mode - download via panel server first (mutually exclusive with github_proxy)
    use_panel_proxy: bool = Field(default=False)
    
    # SSH connection health tracking
    last_ssh_success: Optional[datetime] = Field(default=None)
    last_ssh_failure: Optional[datetime] = Field(default=None)
    consecutive_ssh_failures: int = Field(default=0)
    is_ssh_down: bool = Field(default=False)
    
    # SSH health monitoring daemon configuration
    enable_ssh_health_monitoring: bool = Field(default=True)
    ssh_health_check_interval_hours: int = Field(default=2)  # Check every 2 hours
    ssh_health_failure_threshold: int = Field(default=84)  # 84 failures = 7 days @ 2 hours
    last_ssh_health_check: Optional[datetime] = Field(default=None)
    ssh_health_status: str = Field(default="unknown", max_length=50)  # unknown, healthy, degraded, down, completely_down
    
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
    
    def should_skip_background_checks(self) -> bool:
        """
        Check if server should skip background checks due to prolonged SSH failures
        Returns True if server has been failing SSH for 3+ consecutive days
        """
        if not self.is_ssh_down:
            return False
        
        # Server is marked as down - verify it's still in failure state
        from modules.utils import get_current_time
        from datetime import timezone
        now = get_current_time()
        
        # Helper to make datetime timezone-aware if it's naive
        def ensure_aware(dt):
            if dt is None:
                return None
            if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
                # Assume UTC for naive datetimes from database
                return dt.replace(tzinfo=timezone.utc)
            return dt
        
        # Check days since last successful connection
        if self.last_ssh_success:
            last_success = ensure_aware(self.last_ssh_success)
            days_since_success = (now - last_success).days
            return days_since_success >= 3
        elif hasattr(self, 'created_at') and self.created_at:
            # Never had success - check age of server
            created = ensure_aware(self.created_at)
            days_since_creation = (now - created).days
            return days_since_creation >= 3
        
        # If we don't have enough info, don't skip
        return False
    
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
    async def get_all(cls, session: AsyncSession, skip: int = 0, limit: int = 100) -> List["Server"]:
        """
        Get all servers (admin only) with pagination.
        
        ⚠️ SECURITY WARNING: This method bypasses all user ownership checks.
        It MUST only be called from routes protected by get_current_admin_user.
        Never call this method without proper admin authentication.
        """
        result = await session.execute(
            select(cls).offset(skip).limit(limit)
        )
        return result.scalars().all()
    
    @classmethod
    async def get_by_id(cls, session: AsyncSession, server_id: int) -> Optional["Server"]:
        """
        Get server by ID (without user restriction, for admin).
        
        ⚠️ SECURITY WARNING: This method bypasses all user ownership checks.
        It MUST only be used in conjunction with admin permission validation.
        Use get_by_id_and_user for regular user access.
        """
        result = await session.execute(select(cls).where(cls.id == server_id))
        return result.scalar_one_or_none()
    
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


class PluginCategory(str, enum.Enum):
    """Plugin category enumeration"""
    GAME_MODE = "game_mode"
    ENTERTAINMENT = "entertainment"
    UTILITY = "utility"
    ADMIN = "admin"
    PERFORMANCE = "performance"
    LIBRARY = "library"
    OTHER = "other"


class MarketPlugin(SQLModel, table=True):
    """Plugin market model - stores plugins available for installation"""
    __tablename__ = "market_plugins"
    
    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    github_url: str = Field(max_length=500, nullable=False, unique=True, index=True)
    title: str = Field(max_length=255, nullable=False, index=True)
    description: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    author: Optional[str] = Field(default=None, max_length=255)
    version: Optional[str] = Field(default=None, max_length=50)
    category: PluginCategory = Field(default=PluginCategory.OTHER, sa_column=Column(SQLEnum(PluginCategory), nullable=False))
    tags: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))  # Comma-separated tags
    is_recommended: bool = Field(default=False)
    icon_url: Optional[str] = Field(default=None, max_length=500)
    dependencies: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))  # Comma-separated plugin IDs
    custom_install_path: Optional[str] = Field(default=None, max_length=255)  # Custom extraction path for non-standard packages (e.g., "addons")
    download_count: int = Field(default=0)
    install_count: int = Field(default=0)
    created_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP"})
    updated_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP", "onupdate": func.now()})
    
    def __repr__(self):
        return f"<MarketPlugin(id={self.id}, title='{self.title}', category='{self.category.value}')>"
    
    @classmethod
    async def get_by_id(cls, session: AsyncSession, plugin_id: int) -> Optional["MarketPlugin"]:
        """Get plugin by ID"""
        result = await session.execute(select(cls).where(cls.id == plugin_id))
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_by_github_url(cls, session: AsyncSession, github_url: str) -> Optional["MarketPlugin"]:
        """Get plugin by GitHub URL"""
        result = await session.execute(select(cls).where(cls.github_url == github_url))
        return result.scalar_one_or_none()
    
    @classmethod
    async def search_plugins(
        cls,
        session: AsyncSession,
        category: Optional[PluginCategory] = None,
        search_query: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> tuple[List["MarketPlugin"], int]:
        """
        Search plugins with filters and pagination.
        Returns tuple of (plugins, total_count)
        """
        from sqlalchemy import or_, func as sqlfunc
        
        query = select(cls)
        count_query = select(sqlfunc.count()).select_from(cls)
        
        # Apply category filter
        if category:
            query = query.where(cls.category == category)
            count_query = count_query.where(cls.category == category)
        
        # Apply search query (search in title, description, author)
        if search_query and search_query.strip():
            search_pattern = f"%{search_query.strip()}%"
            search_condition = or_(
                cls.title.like(search_pattern),
                cls.description.like(search_pattern),
                cls.author.like(search_pattern)
            )
            query = query.where(search_condition)
            count_query = count_query.where(search_condition)
        
        # Get total count
        count_result = await session.execute(count_query)
        total_count = count_result.scalar()
        
        # Apply ordering (recommended first, then by install count)
        query = query.order_by(cls.is_recommended.desc(), cls.install_count.desc(), cls.created_at.desc())
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        # Execute query
        result = await session.execute(query)
        plugins = result.scalars().all()
        
        return plugins, total_count


class SSHServerSudo(SQLModel, table=True):
    """SSH Server Sudo Configuration model for setup wizard"""
    __tablename__ = "ssh_servers_sudo"
    
    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    host: str = Field(max_length=255, nullable=False)
    ssh_port: int = Field(default=22, nullable=False)
    sudo_user: str = Field(max_length=100, nullable=False)
    sudo_password: str = Field(max_length=255, nullable=False)
    created_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP"})
    updated_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP", "onupdate": func.now()})
    
    def __repr__(self):
        return f"<SSHServerSudo(id={self.id}, host='{self.host}', port={self.ssh_port}, user='{self.sudo_user}')>"
    
    @classmethod
    async def get_by_unique_key(
        cls, 
        session: AsyncSession, 
        user_id: int, 
        host: str, 
        ssh_port: int, 
        sudo_user: str
    ) -> Optional["SSHServerSudo"]:
        """Get SSH sudo config by unique composite key"""
        result = await session.execute(
            select(cls).where(
                cls.user_id == user_id,
                cls.host == host,
                cls.ssh_port == ssh_port,
                cls.sudo_user == sudo_user
            )
        )
        return result.scalar_one_or_none()
    
    @classmethod
    async def upsert(
        cls,
        session: AsyncSession,
        user_id: int,
        host: str,
        ssh_port: int,
        sudo_user: str,
        sudo_password: str
    ) -> "SSHServerSudo":
        """Insert or update SSH sudo configuration"""
        # Try to get existing record
        existing = await cls.get_by_unique_key(session, user_id, host, ssh_port, sudo_user)
        
        if existing:
            # Update existing record (updated_at is handled by database trigger)
            existing.sudo_password = sudo_password
            session.add(existing)
            await session.commit()
            await session.refresh(existing)
            return existing
        else:
            # Create new record
            new_record = cls(
                user_id=user_id,
                host=host,
                ssh_port=ssh_port,
                sudo_user=sudo_user,
                sudo_password=sudo_password
            )
            session.add(new_record)
            await session.commit()
            await session.refresh(new_record)
            return new_record


# For backward compatibility with existing code that uses Base.metadata
# SQLModel uses SQLModel.metadata instead
Base = SQLModel
