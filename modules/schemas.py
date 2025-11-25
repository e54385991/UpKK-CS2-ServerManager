"""
Pydantic schemas for request/response validation
"""
from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from .models import ServerStatus
import re


# Server action constants
ALLOWED_SERVER_ACTIONS = [
    "deploy", "start", "stop", "restart", "status", "update", "validate",
    "install_metamod", "install_counterstrikesharp", "install_cs2fixes",
    "update_metamod", "update_counterstrikesharp", "update_cs2fixes",
    "backup_plugins"
]
SERVER_ACTION_PATTERN = f"^({'|'.join(ALLOWED_SERVER_ACTIONS)})$"

# Scheduled task action constants (subset of server actions that can be automated)
ALLOWED_SCHEDULED_TASK_ACTIONS = [
    "start", "stop", "restart", "update", "validate"
]
SCHEDULED_TASK_ACTION_PATTERN = f"^({'|'.join(ALLOWED_SCHEDULED_TASK_ACTIONS)})$"


# User schemas
class UserCreate(BaseModel):
    """Schema for user registration"""
    username: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=100)
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")


class UserLogin(BaseModel):
    """Schema for user login"""
    username: str
    password: str
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")


class UserResponse(BaseModel):
    """Schema for user response"""
    id: int
    username: str
    email: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class Token(BaseModel):
    """Schema for JWT token response"""
    access_token: str
    token_type: str


class TokenData(BaseModel):
    """Schema for token data"""
    user_id: Optional[int] = None
    username: Optional[str] = None


class PasswordReset(BaseModel):
    """Schema for password reset"""
    current_password: str = Field(..., min_length=6, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)
    confirm_password: str = Field(..., min_length=6, max_length=100)
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")


class UserProfileUpdate(BaseModel):
    """Schema for updating user profile"""
    email: Optional[EmailStr] = None
    steam_api_key: Optional[str] = Field(None, max_length=64, description="Steam Web API key for game server management")
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")
    
    @field_validator('steam_api_key')
    @classmethod
    def validate_steam_api_key(cls, v):
        """Validate Steam API key format"""
        if v is None or v.strip() == '':
            return v
        # Steam API keys are 32-character hexadecimal strings
        v = v.strip()
        if not re.match(r'^[A-Fa-f0-9]{32}$', v):
            raise ValueError('Steam API key must be a 32-character hexadecimal string')
        return v


class SteamApiKeyResponse(BaseModel):
    """Schema for Steam API key response"""
    steam_api_key: Optional[str]
    
    class Config:
        from_attributes = True


class GenerateServerTokenRequest(BaseModel):
    """Schema for generating game server login token"""
    server_name: Optional[str] = Field(None, max_length=255, description="Optional memo/description for the server")
    captcha_token: str = Field(..., description="CAPTCHA token (required for security)")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="CAPTCHA code (required for security)")


class GenerateServerTokenResponse(BaseModel):
    """Schema for game server login token response"""
    success: bool
    login_token: Optional[str] = None
    error: Optional[str] = None


class ApiKeyResponse(BaseModel):
    """Schema for API key response"""
    api_key: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class ApiKeyGenerate(BaseModel):
    """Schema for generating API key"""
    captcha_token: Optional[str] = Field(None, description="CAPTCHA token from /api/captcha/generate (optional)")
    captcha_code: Optional[str] = Field(None, min_length=4, max_length=4, description="User-entered CAPTCHA code (optional)")



# Server schemas
class ServerCreate(BaseModel):
    """Schema for creating a new server (password authentication only)"""
    name: str = Field(..., min_length=1, max_length=255)
    host: str = Field(..., min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(..., min_length=1, max_length=100)
    ssh_password: str = Field(..., min_length=1, description="SSH password (required)")
    sudo_password: Optional[str] = None
    game_port: int = Field(default=27015, ge=1, le=65535)
    game_directory: str = Field(default="/home/cs2server/cs2")
    description: Optional[str] = None
    
    # CAPTCHA validation
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")
    
    # LGSM-style server configuration
    server_name: str = Field(default="CS2 Server", max_length=255)
    server_password: Optional[str] = None
    rcon_password: Optional[str] = None
    steam_account_token: Optional[str] = Field(None, max_length=255, description="Steam game server login token (GSLT)")
    default_map: str = Field(default="de_dust2", max_length=100)
    max_players: int = Field(default=32, ge=1, le=64)
    tickrate: int = Field(default=128, ge=64, le=128)
    game_mode: str = Field(default="competitive", max_length=50)
    game_type: str = Field(default="0", max_length=50)
    
    # Advanced parameters
    additional_parameters: Optional[str] = None
    ip_address: Optional[str] = None
    client_port: Optional[int] = Field(None, ge=1, le=65535)
    tv_port: Optional[int] = Field(None, ge=1, le=65535)
    tv_enable: bool = Field(default=False)
    
    # Server-to-backend communication
    backend_url: Optional[str] = Field(None, max_length=500, description="Backend URL for status reporting (optional)")
    
    # Auto-cleanup configuration
    auto_clear_crash_hours: Optional[int] = Field(None, ge=0, description="Hours offline before auto-clearing crash history (0 or None = disabled, default 2 hours recommended)")
    
    # Web-based monitoring configuration
    enable_panel_monitoring: bool = Field(default=False, description="Enable web panel monitoring and auto-restart")
    monitor_interval_seconds: int = Field(default=60, ge=10, le=3600, description="How often to check server status in seconds (10-3600)")
    auto_restart_on_crash: bool = Field(default=True, description="Auto-restart if process not found (when monitoring enabled)")
    
    # A2S query configuration
    a2s_query_host: Optional[str] = Field(None, max_length=255, description="A2S query host (defaults to server host if not set)")
    a2s_query_port: Optional[int] = Field(None, ge=1, le=65535, description="A2S query port (defaults to game port if not set)")
    enable_a2s_monitoring: bool = Field(default=False, description="Enable A2S query monitoring")
    a2s_failure_threshold: int = Field(default=3, ge=1, le=10, description="Number of consecutive A2S failures before restart (1-10)")
    a2s_check_interval_seconds: int = Field(default=60, ge=15, le=3600, description="A2S check interval in seconds (15-3600)")
    
    # Auto-update configuration
    current_game_version: Optional[str] = Field(None, max_length=50, description="Current installed CS2 version")
    enable_auto_update: bool = Field(default=True, description="Enable automatic updates based on Steam API version check")
    update_check_interval_hours: int = Field(default=1, ge=1, le=24, description="Hours between version checks (1-24)")
    
    # CPU affinity configuration
    cpu_affinity: Optional[str] = Field(None, max_length=500, description="Comma-separated list of CPU cores (e.g., '0,1,2,3' or '0-3,8-11')")
    
    @field_validator('cpu_affinity')
    @classmethod
    def validate_cpu_affinity(cls, v):
        """Validate CPU affinity format to prevent command injection"""
        if v is None or v.strip() == '':
            return v
        # Only allow digits, commas, and hyphens
        if not re.match(r'^[\d,\-\s]+$', v):
            raise ValueError('CPU affinity must only contain digits, commas, and hyphens')
        return v.strip()
    
    @field_validator('steam_account_token')
    @classmethod
    def validate_steam_account_token(cls, v):
        """Validate Steam account token format to prevent command injection"""
        if v is None or v.strip() == '':
            return v
        # Steam GSLT tokens are alphanumeric with no special characters that could cause shell injection
        v = v.strip()
        if not re.match(r'^[A-Za-z0-9]+$', v):
            raise ValueError('Steam account token must only contain alphanumeric characters')
        return v


class ServerUpdate(BaseModel):
    """Schema for updating a server (password authentication only)"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    host: Optional[str] = Field(None, min_length=1, max_length=255)
    ssh_port: Optional[int] = Field(None, ge=1, le=65535)
    ssh_user: Optional[str] = Field(None, min_length=1, max_length=100)
    ssh_password: Optional[str] = None
    sudo_password: Optional[str] = None
    game_port: Optional[int] = Field(None, ge=1, le=65535)
    game_directory: Optional[str] = None
    description: Optional[str] = None
    
    # LGSM-style server configuration
    server_name: Optional[str] = Field(None, max_length=255)
    server_password: Optional[str] = None
    rcon_password: Optional[str] = None
    steam_account_token: Optional[str] = Field(None, max_length=255, description="Steam game server login token (GSLT)")
    default_map: Optional[str] = Field(None, max_length=100)
    max_players: Optional[int] = Field(None, ge=1, le=64)
    tickrate: Optional[int] = Field(None, ge=64, le=128)
    game_mode: Optional[str] = Field(None, max_length=50)
    game_type: Optional[str] = Field(None, max_length=50)
    
    # Advanced parameters
    additional_parameters: Optional[str] = None
    ip_address: Optional[str] = None
    client_port: Optional[int] = Field(None, ge=1, le=65535)
    tv_port: Optional[int] = Field(None, ge=1, le=65535)
    tv_enable: Optional[bool] = None
    
    # Server-to-backend communication
    backend_url: Optional[str] = Field(None, max_length=500, description="Backend URL for status reporting (optional)")
    
    # Auto-cleanup configuration
    auto_clear_crash_hours: Optional[int] = Field(None, ge=0, description="Hours offline before auto-clearing crash history (0 or None = disabled)")
    
    # Web-based monitoring configuration
    enable_panel_monitoring: Optional[bool] = Field(None, description="Enable web panel monitoring and auto-restart")
    monitor_interval_seconds: Optional[int] = Field(None, ge=10, le=3600, description="How often to check server status in seconds")
    auto_restart_on_crash: Optional[bool] = Field(None, description="Auto-restart if process not found")
    
    # A2S query configuration
    a2s_query_host: Optional[str] = Field(None, max_length=255, description="A2S query host (defaults to server host if not set)")
    a2s_query_port: Optional[int] = Field(None, ge=1, le=65535, description="A2S query port (defaults to game port if not set)")
    enable_a2s_monitoring: Optional[bool] = Field(None, description="Enable A2S query monitoring")
    a2s_failure_threshold: Optional[int] = Field(None, ge=1, le=10, description="Number of consecutive A2S failures before restart")
    a2s_check_interval_seconds: Optional[int] = Field(None, ge=15, le=3600, description="A2S check interval in seconds (15-3600)")
    
    # Auto-update configuration
    current_game_version: Optional[str] = Field(None, max_length=50, description="Current installed CS2 version")
    enable_auto_update: Optional[bool] = Field(None, description="Enable automatic updates based on Steam API version check")
    update_check_interval_hours: Optional[int] = Field(None, ge=1, le=24, description="Hours between version checks (1-24)")
    
    # CPU affinity configuration
    cpu_affinity: Optional[str] = Field(None, max_length=500, description="Comma-separated list of CPU cores (e.g., '0,1,2,3' or '0-3,8-11')")
    
    @field_validator('cpu_affinity')
    @classmethod
    def validate_cpu_affinity(cls, v):
        """Validate CPU affinity format to prevent command injection"""
        if v is None or v.strip() == '':
            return v
        # Only allow digits, commas, and hyphens
        if not re.match(r'^[\d,\-\s]+$', v):
            raise ValueError('CPU affinity must only contain digits, commas, and hyphens')
        return v.strip()
    
    @field_validator('steam_account_token')
    @classmethod
    def validate_steam_account_token(cls, v):
        """Validate Steam account token format to prevent command injection"""
        if v is None or v.strip() == '':
            return v
        # Steam GSLT tokens are alphanumeric with no special characters that could cause shell injection
        v = v.strip()
        if not re.match(r'^[A-Za-z0-9]+$', v):
            raise ValueError('Steam account token must only contain alphanumeric characters')
        return v


class ServerResponse(BaseModel):
    """Schema for server response (password authentication only)"""
    id: int
    user_id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    game_port: int
    game_directory: str
    status: ServerStatus
    description: Optional[str]
    last_deployed: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    # LGSM-style server configuration
    server_name: str
    server_password: Optional[str]
    rcon_password: Optional[str]
    steam_account_token: Optional[str]
    default_map: str
    max_players: int
    tickrate: int
    game_mode: str
    game_type: str
    
    # Advanced parameters
    additional_parameters: Optional[str]
    ip_address: Optional[str]
    client_port: Optional[int]
    tv_port: Optional[int]
    tv_enable: bool
    
    # Server-to-backend communication
    api_key: Optional[str]
    backend_url: Optional[str]
    
    # Auto-cleanup configuration
    auto_clear_crash_hours: Optional[int]
    last_status_check: Optional[datetime]
    
    # Web-based monitoring configuration
    enable_panel_monitoring: bool
    monitor_interval_seconds: int
    auto_restart_on_crash: bool
    
    # A2S query configuration
    a2s_query_host: Optional[str]
    a2s_query_port: Optional[int]
    enable_a2s_monitoring: bool
    a2s_failure_threshold: int
    a2s_check_interval_seconds: int
    
    # Auto-update configuration
    current_game_version: Optional[str]
    enable_auto_update: bool
    update_check_interval_hours: int
    last_update_check: Optional[datetime]
    last_update_time: Optional[datetime]
    
    # CPU affinity configuration
    cpu_affinity: Optional[str]
    
    class Config:
        from_attributes = True


class ServerAction(BaseModel):
    """Schema for server actions"""
    action: str = Field(..., pattern=SERVER_ACTION_PATTERN)


# Batch actions constants - only allow safe batch actions
ALLOWED_BATCH_ACTIONS = ["restart", "stop", "update"]
BATCH_ACTION_PATTERN = f"^({'|'.join(ALLOWED_BATCH_ACTIONS)})$"

# Allowed plugins for batch installation
ALLOWED_PLUGINS = ["metamod", "counterstrikesharp", "cs2fixes"]


class BatchActionRequest(BaseModel):
    """Schema for batch server actions"""
    server_ids: List[int] = Field(..., min_length=1, description="List of server IDs to perform action on")
    action: str = Field(..., pattern=BATCH_ACTION_PATTERN, description="Action to perform on all servers")


class BatchInstallPluginsRequest(BaseModel):
    """Schema for batch plugin installation"""
    server_ids: List[int] = Field(..., min_length=1, description="List of server IDs to install plugins on")
    plugins: List[str] = Field(..., min_length=1, description="List of plugins to install")
    
    @field_validator('plugins')
    @classmethod
    def validate_plugins(cls, v):
        """Validate plugin names"""
        for plugin in v:
            if plugin not in ALLOWED_PLUGINS:
                raise ValueError(f'Invalid plugin: {plugin}. Allowed plugins: {", ".join(ALLOWED_PLUGINS)}')
        return v


class BatchActionResponse(BaseModel):
    """Schema for batch action response"""
    success: bool
    message: str
    batch_id: str = Field(..., description="Unique batch ID for tracking progress")
    server_count: int = Field(..., description="Number of servers in batch")


class ActionResponse(BaseModel):
    """Schema for action response"""
    success: bool
    message: str
    data: Optional[dict] = None


class DeploymentLogResponse(BaseModel):
    """Schema for deployment log response"""
    id: int
    server_id: int
    action: str
    status: str
    output: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


# A2S Cache schemas
class A2SServerInfo(BaseModel):
    """Schema for A2S server information"""
    server_name: Optional[str] = None
    map_name: Optional[str] = None
    folder: Optional[str] = None
    game: Optional[str] = None
    player_count: Optional[int] = None
    max_players: Optional[int] = None
    bot_count: Optional[int] = None
    server_type: Optional[str] = None
    platform: Optional[str] = None
    password_protected: Optional[bool] = None
    vac_enabled: Optional[bool] = None
    version: Optional[str] = None
    ping: Optional[float] = None
    keywords: Optional[str] = None
    game_id: Optional[int] = None


class A2SPlayerInfo(BaseModel):
    """Schema for A2S player information"""
    name: str
    score: int
    duration: float


class A2SCachedData(BaseModel):
    """Schema for cached A2S data for a single server"""
    query_host: str
    query_port: int
    success: bool
    server_info: Optional[A2SServerInfo] = None
    players: List[A2SPlayerInfo] = []
    response_time_ms: int
    timestamp: str
    last_updated: str
    error: Optional[str] = None


class A2SCacheResponse(BaseModel):
    """Schema for A2S cache response containing all servers"""
    servers: Dict[str, A2SCachedData]
    timestamp: str


# Initialized Server schemas
class InitializedServerCreate(BaseModel):
    """Schema for saving an initialized server from setup wizard"""
    name: str = Field(..., min_length=1, max_length=255, description="Friendly name for the server")
    host: str = Field(..., min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(..., min_length=1, max_length=100)
    ssh_password: str = Field(..., min_length=1, max_length=255)
    game_directory: str = Field(default="/home/cs2server/cs2")


class InitializedServerListItem(BaseModel):
    """Schema for initialized server in list (without sensitive data)"""
    id: int
    user_id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    game_directory: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class InitializedServerResponse(BaseModel):
    """Schema for initialized server response (includes password for filling forms)"""
    id: int
    user_id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    ssh_password: str
    game_directory: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# Scheduled Task schemas
class ScheduledTaskCreate(BaseModel):
    """Schema for creating a scheduled task"""
    name: str = Field(..., min_length=1, max_length=255, description="Task name/description")
    action: str = Field(..., pattern=SCHEDULED_TASK_ACTION_PATTERN, description="Action to perform (restart, start, stop, update, validate)")
    enabled: bool = Field(default=True, description="Whether the task is active")
    schedule_type: str = Field(..., description="Schedule type: daily, weekly, interval, cron")
    schedule_value: str = Field(..., min_length=1, max_length=255, description="Time (HH:MM), day+time (MON:14:30), interval (3600), or cron expression")
    
    @field_validator('schedule_type')
    @classmethod
    def validate_schedule_type(cls, v):
        """Validate schedule type"""
        allowed_types = ['daily', 'weekly', 'interval', 'cron']
        if v not in allowed_types:
            raise ValueError(f'Schedule type must be one of: {", ".join(allowed_types)}')
        return v
    
    @field_validator('schedule_value')
    @classmethod
    def validate_schedule_value(cls, v, info):
        """Validate schedule value format based on schedule type"""
        if not v or len(v.strip()) == 0:
            raise ValueError('Schedule value cannot be empty')
        
        # Prevent command injection
        if any(char in v for char in [';', '&', '|', '$', '`', '\n', '\r']):
            raise ValueError('Schedule value contains invalid characters')
        
        v_stripped = v.strip()
        
        # Get schedule_type from context if available
        schedule_type = info.data.get('schedule_type') if hasattr(info, 'data') else None
        
        if schedule_type == 'daily':
            # Validate HH:MM format
            if not re.match(r'^\d{1,2}:\d{2}$', v_stripped):
                raise ValueError('Daily schedule must be in HH:MM format (e.g., 14:30)')
            parts = v_stripped.split(':')
            hour, minute = int(parts[0]), int(parts[1])
            if hour < 0 or hour > 23:
                raise ValueError('Hour must be between 0 and 23')
            if minute < 0 or minute > 59:
                raise ValueError('Minute must be between 0 and 59')
                
        elif schedule_type == 'weekly':
            # Validate DAY:HH:MM format
            if not re.match(r'^[A-Z]{3}:\d{1,2}:\d{2}$', v_stripped.upper()):
                raise ValueError('Weekly schedule must be in DAY:HH:MM format (e.g., MON:14:30)')
            parts = v_stripped.upper().split(':')
            day, hour, minute = parts[0], int(parts[1]), int(parts[2])
            valid_days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
            if day not in valid_days:
                raise ValueError(f'Day must be one of: {", ".join(valid_days)}')
            if hour < 0 or hour > 23:
                raise ValueError('Hour must be between 0 and 23')
            if minute < 0 or minute > 59:
                raise ValueError('Minute must be between 0 and 59')
                
        elif schedule_type == 'interval':
            # Validate positive integer
            try:
                interval = int(v_stripped)
                if interval <= 0:
                    raise ValueError('Interval must be a positive number')
                if interval < 60:
                    raise ValueError('Interval must be at least 60 seconds')
            except ValueError as e:
                if 'positive' in str(e) or 'at least' in str(e):
                    raise
                raise ValueError('Interval must be a valid integer (seconds)')
        
        return v_stripped


class ScheduledTaskUpdate(BaseModel):
    """Schema for updating a scheduled task"""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Task name/description")
    action: Optional[str] = Field(None, pattern=SCHEDULED_TASK_ACTION_PATTERN, description="Action to perform")
    enabled: Optional[bool] = Field(None, description="Whether the task is active")
    schedule_type: Optional[str] = Field(None, description="Schedule type: daily, weekly, interval, cron")
    schedule_value: Optional[str] = Field(None, min_length=1, max_length=255, description="Time or cron expression")
    
    @field_validator('schedule_type')
    @classmethod
    def validate_schedule_type(cls, v):
        """Validate schedule type"""
        if v is not None:
            allowed_types = ['daily', 'weekly', 'interval', 'cron']
            if v not in allowed_types:
                raise ValueError(f'Schedule type must be one of: {", ".join(allowed_types)}')
        return v
    
    @field_validator('schedule_value')
    @classmethod
    def validate_schedule_value(cls, v):
        """Validate schedule value format"""
        if v is not None:
            if len(v.strip()) == 0:
                raise ValueError('Schedule value cannot be empty')
            # Prevent command injection
            if any(char in v for char in [';', '&', '|', '$', '`', '\n', '\r']):
                raise ValueError('Schedule value contains invalid characters')
        return v.strip() if v else v


class ScheduledTaskResponse(BaseModel):
    """Schema for scheduled task response"""
    id: int
    server_id: int
    name: str
    action: str
    enabled: bool
    schedule_type: str
    schedule_value: str
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    run_count: int
    last_status: Optional[str]
    last_error: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True
