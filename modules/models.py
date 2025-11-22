"""
Database models for CS2 Server Manager
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Enum as SQLEnum, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum

Base = declarative_base()


class AuthType(enum.Enum):
    """SSH Authentication types"""
    PASSWORD = "password"
    KEY_FILE = "key_file"


class ServerStatus(enum.Enum):
    """Server status enumeration"""
    PENDING = "pending"
    DEPLOYING = "deploying"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


class User(Base):
    """User model for authentication and authorization"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', email='{self.email}')>"


class Server(Base):
    """CS2 Server model"""
    __tablename__ = "servers"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    host = Column(String(255), nullable=False)
    ssh_port = Column(Integer, default=22)
    ssh_user = Column(String(100), nullable=False)
    auth_type = Column(SQLEnum(AuthType), nullable=False)
    ssh_password = Column(String(255), nullable=True)  # For password auth
    ssh_key_path = Column(String(500), nullable=True)  # For key file auth
    sudo_password = Column(String(255), nullable=True)  # For sudo commands (if needed)
    
    # Server configuration
    game_port = Column(Integer, default=27015)
    game_directory = Column(String(500), default="/home/cs2server/cs2")
    status = Column(SQLEnum(ServerStatus), default=ServerStatus.PENDING)
    
    # LGSM-style server start parameters
    server_name = Column(String(255), default="CS2 Server")  # Server hostname
    server_password = Column(String(255), nullable=True)  # Server password (rcon password)
    rcon_password = Column(String(255), nullable=True)  # RCON password
    default_map = Column(String(100), default="de_dust2")  # Default map
    max_players = Column(Integer, default=32)  # Maximum players
    tickrate = Column(Integer, default=128)  # Server tickrate (64 or 128)
    game_mode = Column(String(50), default="competitive")  # Game mode
    game_type = Column(String(50), default="0")  # Game type
    
    # Advanced start parameters
    additional_parameters = Column(Text, nullable=True)  # Custom command-line parameters
    ip_address = Column(String(100), nullable=True)  # Bind IP address
    client_port = Column(Integer, nullable=True)  # Client port (default: game_port + 1)
    tv_port = Column(Integer, nullable=True)  # SourceTV port
    tv_enable = Column(Boolean, default=False)  # Enable SourceTV
    
    # Server-to-backend communication
    api_key = Column(String(64), nullable=True, unique=True, index=True)  # Unique API key for server status reporting
    backend_url = Column(String(500), nullable=True)  # Backend URL for status reporting (optional, uses global setting if not set)
    
    # Auto-cleanup configuration
    auto_clear_crash_hours = Column(Integer, nullable=True)  # Hours offline before auto-clearing crash history (0 or None = disabled)
    last_status_check = Column(DateTime, nullable=True)  # Last time status was checked
    
    # Web-based monitoring configuration (independent of local autorestart)
    enable_panel_monitoring = Column(Boolean, default=False)  # Enable web panel monitoring and auto-restart
    monitor_interval_seconds = Column(Integer, default=60)  # How often to check server status (default: 60 seconds)
    auto_restart_on_crash = Column(Boolean, default=True)  # Auto-restart if process not found (when monitoring enabled)
    
    # A2S query configuration for server monitoring
    a2s_query_host = Column(String(255), nullable=True)  # A2S query host (defaults to 'host' if not set)
    a2s_query_port = Column(Integer, nullable=True)  # A2S query port (defaults to 'game_port' if not set)
    enable_a2s_monitoring = Column(Boolean, default=False)  # Enable A2S query monitoring
    a2s_failure_threshold = Column(Integer, default=3)  # Number of consecutive A2S failures before restart (default: 3)
    a2s_check_interval_seconds = Column(Integer, default=60)  # A2S check interval in seconds (default: 60, minimum: 15)
    
    # Auto-update configuration
    current_game_version = Column(String(50), nullable=True)  # Current installed CS2 version (from A2S or manual)
    enable_auto_update = Column(Boolean, default=True)  # Enable automatic updates based on Steam API version check
    update_check_interval_hours = Column(Integer, default=1)  # Hours between version checks (default: 1 hour)
    last_update_check = Column(DateTime, nullable=True)  # Last time version was checked against Steam API
    last_update_time = Column(DateTime, nullable=True)  # Last time server was updated
    
    # CPU affinity configuration
    cpu_affinity = Column(String(500), nullable=True)  # Comma-separated list of CPU cores (e.g., "0,1,2,3" or "0-3,8-11")
    
    # Additional info
    description = Column(Text, nullable=True)
    last_deployed = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<Server(id={self.id}, name='{self.name}', host='{self.host}', status='{self.status.value}')>"


class DeploymentLog(Base):
    """Deployment log model"""
    __tablename__ = "deployment_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, nullable=False, index=True)
    action = Column(String(50), nullable=False)  # deploy, start, stop, restart
    status = Column(String(50), nullable=False)  # success, failed, in_progress
    output = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    
    def __repr__(self):
        return f"<DeploymentLog(id={self.id}, server_id={self.server_id}, action='{self.action}', status='{self.status}')>"


class MonitoringLog(Base):
    """Panel monitoring log model - stores monitoring events and auto-restart activities"""
    __tablename__ = "monitoring_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # status_check, auto_restart, monitoring_start, monitoring_stop
    status = Column(String(50), nullable=False)  # success, failed, info, warning
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    
    def __repr__(self):
        return f"<MonitoringLog(id={self.id}, server_id={self.server_id}, event_type='{self.event_type}', status='{self.status}')>"


class InitializedServer(Base):
    """Initialized server configuration from setup wizard"""
    __tablename__ = "initialized_servers"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)  # User-friendly name for this server
    host = Column(String(255), nullable=False)
    ssh_port = Column(Integer, default=22)
    ssh_user = Column(String(100), nullable=False)  # Usually cs2server from setup wizard
    ssh_password = Column(String(255), nullable=False)  # Password set during initialization
    game_directory = Column(String(500), default="/home/cs2server/cs2")  # Game directory from setup
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<InitializedServer(id={self.id}, user_id={self.user_id}, name='{self.name}', host='{self.host}')>"
