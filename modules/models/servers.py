"""Servers models."""

# ruff: noqa: F403,F405

from .common import *


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
    status: ServerStatus = Field(
        default=ServerStatus.PENDING,
        sa_column=Column(SQLEnum(ServerStatus), default=ServerStatus.PENDING),
    )

    # LGSM-style server start parameters
    server_name: str = Field(default="CS2 Server", max_length=255)
    server_password: Optional[str] = Field(default=None, max_length=255)
    rcon_password: Optional[str] = Field(default=None, max_length=255)
    steam_account_token: Optional[str] = Field(default=None, max_length=255)
    default_map: str = Field(default="de_dust2", max_length=100)
    max_players: int = Field(default=32)
    game_mode: str = Field(default="competitive", max_length=50)
    game_type: str = Field(default="0", max_length=50)

    # Advanced start parameters
    additional_parameters: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
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
    # Persisted user intent. This is deliberately separate from ``status``,
    # which records the last observed runtime state rather than whether
    # background services are allowed to start the server.
    manual_stop_requested: bool = Field(default=False)

    # A2S query configuration for server monitoring
    a2s_query_host: Optional[str] = Field(default=None, max_length=255)
    a2s_query_port: Optional[int] = Field(default=None)
    enable_a2s_monitoring: bool = Field(default=False)
    a2s_failure_threshold: int = Field(default=3)
    a2s_check_interval_seconds: int = Field(default=60)

    # Auto-update configuration
    current_game_version: Optional[str] = Field(default=None, max_length=50)
    enable_auto_update: bool = Field(default=True)
    update_check_interval_hours: float = Field(
        default=1.0
    )  # Support fractional hours (e.g., 0.0167 = 1 minute)
    last_update_check: Optional[datetime] = Field(default=None)
    last_update_time: Optional[datetime] = Field(default=None)

    # Plugin auto-update configuration
    enable_plugin_auto_update: bool = Field(default=False)
    plugin_update_check_interval_hours: float = Field(default=1.0)
    last_plugin_update_check: Optional[datetime] = Field(default=None)
    enable_plugin_post_update_commands: bool = Field(default=False)
    plugin_post_update_command_ids: List[int] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=True)
    )

    # MapChooser custom remote map-pool synchronization
    map_pool_sync_url: Optional[str] = Field(default=None, max_length=4096)

    # CPU affinity configuration
    cpu_affinity: Optional[str] = Field(default=None, max_length=500)

    # Detached console session manager (existing migrated rows remain on screen)
    session_manager: str = Field(default="tmux", max_length=16)

    # GitHub proxy configuration
    github_proxy: Optional[str] = Field(default=None, max_length=500)

    # Panel proxy mode - download via panel server first (mutually exclusive with github_proxy)
    use_panel_proxy: bool = Field(default=False)

    # Discord notification configuration
    discord_notifications_enabled: bool = Field(default=False)
    discord_webhook_url: Optional[str] = Field(default=None, max_length=1000)
    discord_channel_name: Optional[str] = Field(default=None, max_length=255)
    discord_notify_auto_updates: bool = Field(default=True)
    discord_notify_manual_updates: bool = Field(default=True)
    discord_notify_plugin_updates: bool = Field(default=True)
    discord_notify_s3_backups: bool = Field(default=True)
    discord_notify_crash_restarts: bool = Field(default=True)
    discord_crash_restart_min_interval_minutes: int = Field(default=10)

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
    ssh_health_status: str = Field(
        default="unknown", max_length=50
    )  # unknown, healthy, degraded, down, completely_down

    # Additional info
    description: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    last_deployed: Optional[datetime] = Field(default=None)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )

    def __repr__(self):
        status_val = self.status.value if self.status else "unknown"
        return (
            f"<Server(id={self.id}, name='{self.name}', host='{self.host}', status='{status_val}')>"
        )

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
        from datetime import timezone

        from modules.utils import get_current_time

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
        elif hasattr(self, "created_at") and self.created_at:
            # Never had success - check age of server
            created = ensure_aware(self.created_at)
            days_since_creation = (now - created).days
            return days_since_creation >= 3

        # If we don't have enough info, don't skip
        return False

    @classmethod
    async def get_by_id_and_user(
        cls, session: AsyncSession, server_id: int, user_id: int
    ) -> Optional["Server"]:
        """Get server by ID and user ID - common pattern in this application"""
        result = await session.execute(
            select(cls).where(cls.id == server_id, cls.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def get_by_name_and_user(
        cls, session: AsyncSession, name: str, user_id: int
    ) -> Optional["Server"]:
        """Check if server name exists for a user"""
        result = await session.execute(select(cls).where(cls.name == name, cls.user_id == user_id))
        return result.scalar_one_or_none()

    @classmethod
    async def get_by_host_directory_and_user(
        cls, session: AsyncSession, host: str, game_directory: str, user_id: int
    ) -> Optional["Server"]:
        """Check if server with same host and directory exists for a user"""
        result = await session.execute(
            select(cls).where(
                cls.host == host, cls.game_directory == game_directory, cls.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    @classmethod
    async def get_all_by_user(
        cls, session: AsyncSession, user_id: int, skip: int = 0, limit: int = 100
    ) -> List["Server"]:
        """Get all servers for a user with pagination"""
        result = await session.execute(
            select(cls).where(cls.user_id == user_id).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @classmethod
    async def get_all(
        cls, session: AsyncSession, skip: int = 0, limit: int = 100
    ) -> List["Server"]:
        """
        Get all servers (admin only) with pagination.

        ⚠️ SECURITY WARNING: This method bypasses all user ownership checks.
        It MUST only be called from routes protected by get_current_admin_user.
        Never call this method without proper admin authentication.
        """
        result = await session.execute(select(cls).offset(skip).limit(limit))
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
        result = await session.execute(select(cls).where(cls.enable_panel_monitoring.is_(True)))
        return result.scalars().all()

    @classmethod
    async def get_all_with_auto_update(cls, session: AsyncSession) -> List["Server"]:
        """Get all servers with auto-update enabled"""
        result = await session.execute(select(cls).where(cls.enable_auto_update.is_(True)))
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
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )

    def __repr__(self):
        return f"<DeploymentLog(id={self.id}, server_id={self.server_id}, action='{self.action}', status='{self.status}')>"

    @classmethod
    async def get_logs_by_server(
        cls, session: AsyncSession, server_id: int, skip: int = 0, limit: int = 50
    ) -> List["DeploymentLog"]:
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
    created_at: Optional[datetime] = Field(
        default=None, index=True, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )

    def __repr__(self):
        return f"<MonitoringLog(id={self.id}, server_id={self.server_id}, event_type='{self.event_type}', status='{self.status}')>"


class ScheduledTask(SQLModel, table=True):
    """Scheduled task model for automated server operations"""

    __tablename__ = "scheduled_tasks"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    server_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
        )
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

    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )

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
    async def get_by_id_and_server(
        cls, session: AsyncSession, task_id: int, server_id: int
    ) -> Optional["ScheduledTask"]:
        """Get scheduled task by ID and server ID"""
        result = await session.execute(
            select(cls).where(cls.id == task_id, cls.server_id == server_id)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def get_all_by_server(
        cls, session: AsyncSession, server_id: int
    ) -> List["ScheduledTask"]:
        """Get all scheduled tasks for a server"""
        result = await session.execute(
            select(cls).where(cls.server_id == server_id).order_by(cls.id.desc())
        )
        return result.scalars().all()


class CustomCommand(SQLModel, table=True):
    """Saved quick command for a server"""

    __tablename__ = "custom_commands"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    server_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    name: str = Field(max_length=255, nullable=False)
    target: str = Field(default="host", max_length=30, nullable=False)
    commands: str = Field(sa_column=Column(Text, nullable=False))
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )

    def __repr__(self):
        return f"<CustomCommand(id={self.id}, server_id={self.server_id}, name='{self.name}', target='{self.target}')>"

    @classmethod
    async def get_all_by_server_and_user(
        cls, session: AsyncSession, server_id: int, user_id: int
    ) -> List["CustomCommand"]:
        result = await session.execute(
            select(cls)
            .where(cls.server_id == server_id, cls.user_id == user_id)
            .order_by(cls.created_at.desc(), cls.id.desc())
        )
        return result.scalars().all()

    @classmethod
    async def get_by_id_server_and_user(
        cls, session: AsyncSession, command_id: int, server_id: int, user_id: int
    ) -> Optional["CustomCommand"]:
        result = await session.execute(
            select(cls).where(
                cls.id == command_id,
                cls.server_id == server_id,
                cls.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()


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
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )

    def __repr__(self):
        return f"<InitializedServer(id={self.id}, user_id={self.user_id}, name='{self.name}', host='{self.host}')>"

    @classmethod
    async def get_all_by_user(
        cls, session: AsyncSession, user_id: int
    ) -> List["InitializedServer"]:
        """Get all initialized servers for a user"""
        result = await session.execute(
            select(cls).where(cls.user_id == user_id).order_by(cls.created_at.desc())
        )
        return result.scalars().all()
