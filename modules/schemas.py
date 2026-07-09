"""
Pydantic schemas for request/response validation
Using SQLModel for seamless FastAPI integration
"""
from sqlmodel import SQLModel, Field
from pydantic import EmailStr, field_validator, model_validator
from typing import Optional, Dict, List, Annotated
from datetime import datetime
from .models import ServerStatus
import re


# Server action constants
ALLOWED_SERVER_ACTIONS = [
    "deploy", "start", "stop", "restart", "status", "update", "validate",
    "install_metamod", "install_counterstrikesharp", "install_cs2fixes",
    "install_swiftly",
    "update_metamod", "update_counterstrikesharp", "update_cs2fixes",
    "update_swiftly",
    "backup_plugins"
]
SERVER_ACTION_PATTERN = f"^({'|'.join(ALLOWED_SERVER_ACTIONS)})$"

# Scheduled task action constants (subset of server actions that can be automated)
ALLOWED_SCHEDULED_TASK_ACTIONS = [
    "start", "stop", "restart", "update", "validate", "backup_plugins"
]
SCHEDULED_TASK_ACTION_PATTERN = f"^({'|'.join(ALLOWED_SCHEDULED_TASK_ACTIONS)})$"


# User schemas using SQLModel (without table=True, they are Pydantic-like models)
class UserCreate(SQLModel):
    """Schema for user registration"""
    username: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=100)
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")


class UserLogin(SQLModel):
    """Schema for user login"""
    username: str
    password: str
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")


class UserResponse(SQLModel):
    """Schema for user response"""
    id: int
    username: str
    email: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    
    model_config = {"from_attributes": True}


class Token(SQLModel):
    """Schema for JWT token response"""
    access_token: str
    token_type: str


class TokenData(SQLModel):
    """Schema for token data"""
    user_id: Optional[int] = None
    username: Optional[str] = None


class PasswordReset(SQLModel):
    """Schema for password reset"""
    current_password: str = Field(..., min_length=6, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)
    confirm_password: str = Field(..., min_length=6, max_length=100)
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")


class UserProfileUpdate(SQLModel):
    """Schema for updating user profile"""
    email: Optional[EmailStr] = None
    steam_api_key: Optional[str] = Field(None, max_length=64, description="Steam Web API key for game server management")
    github_token: Optional[str] = Field(None, max_length=255, description="GitHub Fine-grained personal access token for accessing private repositories and better rate limits")
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
    
    @field_validator('github_token')
    @classmethod
    def validate_github_token(cls, v):
        """Validate GitHub token format"""
        if v is None or v.strip() == '':
            return v
        # GitHub Fine-grained tokens start with 'github_pat_' followed by alphanumeric characters
        # Example: [REDACTED]
        # Classic tokens start with 'ghp_', 'gho_', 'ghu_', 'ghs_', or 'ghr_' followed by alphanumeric characters
        v = v.strip()
        # More flexible pattern to match real GitHub tokens
        # Fine-grained: github_pat_ + base62-like characters (letters, numbers, underscore)
        # Classic: gh[poushр]_ + base62-like characters
        if not re.match(r'^(github_pat_[A-Za-z0-9_]+|gh[poushр]_[A-Za-z0-9_]+)$', v):
            raise ValueError('GitHub token must be a valid Fine-grained or Classic personal access token')
        return v


class SteamApiKeyResponse(SQLModel):
    """Schema for Steam API key response"""
    steam_api_key: Optional[str] = None
    
    model_config = {"from_attributes": True}


class GitHubTokenStatusResponse(SQLModel):
    """Schema for GitHub token status response"""
    has_token: bool
    token_prefix: Optional[str] = None  # Shows first part like "github_pat_11..." without revealing full token
    
    model_config = {"from_attributes": True}


class S3SettingsResponse(SQLModel):
    """Schema for S3 backup settings without exposing the secret key"""
    enabled: bool
    endpoint_url: Optional[str] = None
    region: Optional[str] = None
    bucket: Optional[str] = None
    access_key_id: Optional[str] = None
    prefix: Optional[str] = None
    use_ssl: bool = True
    retention_count: int = 10
    has_secret: bool = False
    is_configured: bool = False


class S3SettingsUpdate(SQLModel):
    """Schema for updating S3 backup settings"""
    enabled: Optional[bool] = None
    endpoint_url: Optional[str] = Field(None, max_length=500)
    region: Optional[str] = Field(None, max_length=100)
    bucket: Optional[str] = Field(None, max_length=255)
    access_key_id: Optional[str] = Field(None, max_length=255)
    secret_access_key: Optional[str] = Field(None, max_length=255)
    prefix: Optional[str] = Field(None, max_length=255)
    use_ssl: Optional[bool] = None
    retention_count: Optional[int] = Field(None, ge=1, le=10000)
    clear_secret: bool = False
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")

    @field_validator('endpoint_url', 'region', 'bucket', 'access_key_id', 'secret_access_key', 'prefix')
    @classmethod
    def strip_optional_strings(cls, v):
        if v is None:
            return v
        return v.strip()

    @field_validator('bucket')
    @classmethod
    def validate_bucket(cls, v):
        if v is None or v == "":
            return v
        if "/" in v or "\\" in v:
            raise ValueError("S3 bucket name cannot contain slashes")
        return v

    @field_validator('prefix')
    @classmethod
    def validate_prefix(cls, v):
        if v is None or v == "":
            return v
        if any(char in v for char in ['\\', '\n', '\r']):
            raise ValueError("S3 prefix contains invalid characters")
        return v.strip("/")


class S3BackupItem(SQLModel):
    """Schema for a listed S3 backup object"""
    key: str
    filename: str
    size: int
    last_modified: Optional[datetime] = None
    etag: Optional[str] = None


class S3RestoreRequest(SQLModel):
    """Schema for restoring a selected S3 backup"""
    object_key: str = Field(..., min_length=1, max_length=1024)

    @field_validator('object_key')
    @classmethod
    def validate_object_key(cls, v):
        key = v.strip()
        if not key or key.startswith("/") or "\\" in key:
            raise ValueError("Invalid S3 object key")
        if any(char in key for char in ['\n', '\r', '\x00']):
            raise ValueError("S3 object key contains invalid characters")
        return key


class CleanupItem(SQLModel):
    """Schema for a game directory cleanup candidate"""
    path: str
    name: str
    type: str
    size: int = 0
    modified: Optional[float] = None
    category: str
    reason: str
    danger_level: str


class CleanupWorkshopSummary(SQLModel):
    """Schema for Steam Workshop cleanup summary"""
    path: str
    item_count: int = 0
    size: int = 0
    items: List[CleanupItem] = Field(default_factory=list)


class CleanupScanResponse(SQLModel):
    """Schema for game directory cleanup scan response"""
    safe_items: List[CleanupItem] = Field(default_factory=list)
    archive_items: List[CleanupItem] = Field(default_factory=list)
    workshop_summary: CleanupWorkshopSummary
    total_size: int = 0


class CleanupDeleteRequest(SQLModel):
    """Schema for deleting cleanup candidates"""
    mode: str = Field(..., description="Cleanup mode: safe, archives, or workshop")
    paths: List[str] = Field(default_factory=list)
    confirmation_text: Optional[str] = None

    @field_validator('mode')
    @classmethod
    def validate_mode(cls, v):
        mode = v.strip()
        allowed_modes = ["safe", "archives", "workshop"]
        if mode not in allowed_modes:
            raise ValueError(f"Cleanup mode must be one of: {', '.join(allowed_modes)}")
        return mode

    @field_validator('paths')
    @classmethod
    def validate_paths(cls, v):
        clean_paths = []
        for path in v:
            path = path.strip()
            if not path or "\x00" in path or "\n" in path or "\r" in path:
                raise ValueError("Cleanup paths contain invalid characters")
            clean_paths.append(path)
        return clean_paths


class CleanupFailedItem(SQLModel):
    """Schema for a cleanup deletion failure"""
    path: str
    error: str


class CleanupDeleteResponse(SQLModel):
    """Schema for cleanup delete response"""
    success: bool
    message: str
    deleted_count: int = 0
    freed_bytes_estimate: int = 0
    failed_items: List[CleanupFailedItem] = Field(default_factory=list)


class GenerateServerTokenRequest(SQLModel):
    """Schema for generating game server login token"""
    server_name: Optional[str] = Field(None, max_length=255, description="Optional memo/description for the server")
    captcha_token: str = Field(..., description="CAPTCHA token (required for security)")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="CAPTCHA code (required for security)")


class GenerateServerTokenResponse(SQLModel):
    """Schema for game server login token response"""
    success: bool
    login_token: Optional[str] = None
    error: Optional[str] = None


class ApiKeyResponse(SQLModel):
    """Schema for API key response"""
    api_key: str
    created_at: datetime
    
    model_config = {"from_attributes": True}


class ApiKeyGenerate(SQLModel):
    """Schema for generating API key"""
    captcha_token: Optional[str] = Field(None, description="CAPTCHA token from /api/captcha/generate (optional)")
    captcha_code: Optional[str] = Field(None, min_length=4, max_length=4, description="User-entered CAPTCHA code (optional)")



# Server schemas
class ServerCreate(SQLModel):
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
    update_check_interval_hours: float = Field(default=1.0, ge=0.0167, le=24.0, description="Hours between version checks (0.0167-24, where 0.0167≈1 minute)")
    
    # CPU affinity configuration
    cpu_affinity: Optional[str] = Field(None, max_length=500, description="Comma-separated list of CPU cores (e.g., '0,1,2,3' or '0-3,8-11')")
    
    # GitHub proxy configuration
    github_proxy: Optional[str] = Field(None, max_length=500, description="GitHub proxy URL (e.g., https://ghfast.top/https://github.com)")
    
    # Panel proxy mode (mutually exclusive with github_proxy)
    use_panel_proxy: bool = Field(default=False, description="Use panel server as proxy for all downloads (SteamCMD, GitHub). Mutually exclusive with github_proxy.")
    
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
    
    @model_validator(mode='after')
    def validate_proxy_mutual_exclusivity(self):
        """Ensure github_proxy and use_panel_proxy are mutually exclusive"""
        if self.github_proxy and self.use_panel_proxy:
            raise ValueError('github_proxy and use_panel_proxy are mutually exclusive. Please choose only one.')
        return self


class ServerUpdate(SQLModel):
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
    update_check_interval_hours: Optional[float] = Field(None, ge=0.0167, le=24.0, description="Hours between version checks (0.0167-24, where 0.0167≈1 minute)")
    
    # CPU affinity configuration
    cpu_affinity: Optional[str] = Field(None, max_length=500, description="Comma-separated list of CPU cores (e.g., '0,1,2,3' or '0-3,8-11')")
    
    # GitHub proxy configuration
    github_proxy: Optional[str] = Field(None, max_length=500, description="GitHub proxy URL (e.g., https://ghfast.top/https://github.com)")
    
    # Panel proxy mode (mutually exclusive with github_proxy)
    use_panel_proxy: Optional[bool] = Field(None, description="Use panel server as proxy for all downloads (SteamCMD, GitHub). Mutually exclusive with github_proxy.")
    
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
    
    @model_validator(mode='after')
    def validate_proxy_mutual_exclusivity(self):
        """Ensure github_proxy and use_panel_proxy are mutually exclusive"""
        if self.github_proxy and self.use_panel_proxy:
            raise ValueError('github_proxy and use_panel_proxy are mutually exclusive. Please choose only one.')
        return self


class ServerResponse(SQLModel):
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
    description: Optional[str] = None
    last_deployed: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    # LGSM-style server configuration
    server_name: str
    server_password: Optional[str] = None
    rcon_password: Optional[str] = None
    steam_account_token: Optional[str] = None
    default_map: str
    max_players: int
    game_mode: str
    game_type: str
    
    # Advanced parameters
    additional_parameters: Optional[str] = None
    ip_address: Optional[str] = None
    client_port: Optional[int] = None
    tv_port: Optional[int] = None
    tv_enable: bool
    
    # Server-to-backend communication
    api_key: Optional[str] = None
    backend_url: Optional[str] = None
    
    # Auto-cleanup configuration
    auto_clear_crash_hours: Optional[int] = None
    last_status_check: Optional[datetime] = None
    
    # Web-based monitoring configuration
    enable_panel_monitoring: bool
    monitor_interval_seconds: int
    auto_restart_on_crash: bool
    
    # A2S query configuration
    a2s_query_host: Optional[str] = None
    a2s_query_port: Optional[int] = None
    enable_a2s_monitoring: bool
    a2s_failure_threshold: int
    a2s_check_interval_seconds: int
    
    # Auto-update configuration
    current_game_version: Optional[str] = None
    enable_auto_update: bool
    update_check_interval_hours: float
    last_update_check: Optional[datetime] = None
    last_update_time: Optional[datetime] = None
    
    # CPU affinity configuration
    cpu_affinity: Optional[str] = None
    
    # GitHub proxy configuration
    github_proxy: Optional[str] = None
    
    # Panel proxy mode
    use_panel_proxy: bool
    
    # Restart required flag (set by update endpoint when startup-affecting settings change)
    restart_required: bool = False
    
    model_config = {"from_attributes": True}


class ServerResponseWithUser(ServerResponse):
    """Schema for server response with user information (admin only)"""
    user: Optional[UserResponse] = None
    
    model_config = {"from_attributes": True}


class ServerAction(SQLModel):
    """Schema for server actions"""
    action: str = Field(..., description="Server action to perform")
    
    @field_validator('action')
    @classmethod
    def validate_action(cls, v):
        """Validate action matches allowed pattern"""
        if not re.match(SERVER_ACTION_PATTERN, v):
            raise ValueError(f'Invalid action: {v}. Allowed actions: {", ".join(ALLOWED_SERVER_ACTIONS)}')
        return v


# Batch actions constants - only allow safe batch actions
ALLOWED_BATCH_ACTIONS = ["restart", "stop", "update"]
BATCH_ACTION_PATTERN = f"^({'|'.join(ALLOWED_BATCH_ACTIONS)})$"

# Allowed plugins for batch installation
ALLOWED_PLUGINS = ["metamod", "counterstrikesharp", "cs2fixes"]


class BatchActionRequest(SQLModel):
    """Schema for batch server actions"""
    server_ids: List[int] = Field(..., min_length=1, description="List of server IDs to perform action on")
    action: str = Field(..., description="Action to perform on all servers")
    
    @field_validator('action')
    @classmethod
    def validate_action(cls, v):
        """Validate action matches allowed pattern"""
        if not re.match(BATCH_ACTION_PATTERN, v):
            raise ValueError(f'Invalid action: {v}. Allowed actions: {", ".join(ALLOWED_BATCH_ACTIONS)}')
        return v


class BatchInstallPluginsRequest(SQLModel):
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


class BatchActionResponse(SQLModel):
    """Schema for batch action response"""
    success: bool
    message: str
    batch_id: str = Field(..., description="Unique batch ID for tracking progress")
    server_count: int = Field(..., description="Number of servers in batch")


class BatchSendCommandRequest(SQLModel):
    """Schema for batch send command to game servers"""
    server_ids: List[int] = Field(..., min_length=1, description="List of server IDs to send command to")
    command: str = Field(..., min_length=1, max_length=500, description="Command to send to game servers")
    
    @field_validator('command')
    @classmethod
    def validate_command(cls, v):
        """Validate command is not empty and trim whitespace"""
        v = v.strip()
        if not v:
            raise ValueError('Command cannot be empty')
        return v


CUSTOM_COMMAND_TARGETS = ["game_process", "host"]


def _validate_custom_command_text(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError('Commands cannot be empty')
    if '\x00' in v:
        raise ValueError('Commands contain invalid null characters')
    command_lines = [line.strip() for line in v.splitlines() if line.strip()]
    if not command_lines:
        raise ValueError('At least one command line is required')
    if len(command_lines) > 100:
        raise ValueError('At most 100 command lines are allowed')
    for line in command_lines:
        if len(line) > 2000:
            raise ValueError('Each command line must be at most 2000 characters')
    return "\n".join(command_lines)


class CustomCommandCreate(SQLModel):
    """Schema for creating a saved quick command"""
    name: str = Field(..., min_length=1, max_length=255)
    target: str = Field(default="host", description="Send target: game_process or host")
    commands: str = Field(..., min_length=1, max_length=20000)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError('Name cannot be empty')
        return v

    @field_validator('target')
    @classmethod
    def validate_target(cls, v):
        v = v.strip()
        if v not in CUSTOM_COMMAND_TARGETS:
            raise ValueError(f'Target must be one of: {", ".join(CUSTOM_COMMAND_TARGETS)}')
        return v

    @field_validator('commands')
    @classmethod
    def validate_commands(cls, v):
        return _validate_custom_command_text(v)


class CustomCommandUpdate(SQLModel):
    """Schema for updating a saved quick command"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    target: Optional[str] = Field(None, description="Send target: game_process or host")
    commands: Optional[str] = Field(None, min_length=1, max_length=20000)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError('Name cannot be empty')
        return v

    @field_validator('target')
    @classmethod
    def validate_target(cls, v):
        if v is None:
            return v
        v = v.strip()
        if v not in CUSTOM_COMMAND_TARGETS:
            raise ValueError(f'Target must be one of: {", ".join(CUSTOM_COMMAND_TARGETS)}')
        return v

    @field_validator('commands')
    @classmethod
    def validate_commands(cls, v):
        if v is None:
            return v
        return _validate_custom_command_text(v)


class CustomCommandExecuteRequest(SQLModel):
    """Schema for one-time custom command execution"""
    target: str = Field(default="host", description="Send target: game_process or host")
    commands: str = Field(..., min_length=1, max_length=20000)

    @field_validator('target')
    @classmethod
    def validate_target(cls, v):
        v = v.strip()
        if v not in CUSTOM_COMMAND_TARGETS:
            raise ValueError(f'Target must be one of: {", ".join(CUSTOM_COMMAND_TARGETS)}')
        return v

    @field_validator('commands')
    @classmethod
    def validate_commands(cls, v):
        return _validate_custom_command_text(v)


class CustomCommandResponse(SQLModel):
    """Schema for saved quick command response"""
    id: int
    user_id: int
    server_id: int
    name: str
    target: str
    commands: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ActionResponse(SQLModel):
    """Schema for action response"""
    success: bool
    message: str
    data: Optional[dict] = None


class DeploymentLogResponse(SQLModel):
    """Schema for deployment log response"""
    id: int
    server_id: int
    action: str
    status: str
    output: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    
    model_config = {"from_attributes": True}


# A2S Cache schemas
class A2SServerInfo(SQLModel):
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


class A2SPlayerInfo(SQLModel):
    """Schema for A2S player information"""
    name: str
    score: int
    duration: float


class A2SCachedData(SQLModel):
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


class A2SCacheResponse(SQLModel):
    """Schema for A2S cache response containing all servers"""
    servers: Dict[str, A2SCachedData]
    timestamp: str


# Initialized Server schemas
class InitializedServerCreate(SQLModel):
    """Schema for saving an initialized server from setup wizard"""
    name: str = Field(..., min_length=1, max_length=255, description="Friendly name for the server")
    host: str = Field(..., min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(..., min_length=1, max_length=100)
    ssh_password: str = Field(..., min_length=1, max_length=255)
    game_directory: str = Field(default="/home/cs2server/cs2")


class InitializedServerListItem(SQLModel):
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
    
    model_config = {"from_attributes": True}


class InitializedServerResponse(SQLModel):
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
    
    model_config = {"from_attributes": True}


# Scheduled Task schemas
class ScheduledTaskCreate(SQLModel):
    """Schema for creating a scheduled task"""
    name: str = Field(..., min_length=1, max_length=255, description="Task name/description")
    action: str = Field(..., description="Action to perform (restart, start, stop, update, validate, backup_plugins)")
    enabled: bool = Field(default=True, description="Whether the task is active")
    schedule_type: str = Field(..., description="Schedule type: daily, weekly, interval, cron")
    schedule_value: str = Field(..., min_length=1, max_length=255, description="Time (HH:MM), day+time (MON:14:30), interval (3600), or cron expression")
    
    @field_validator('action')
    @classmethod
    def validate_action(cls, v):
        """Validate action matches allowed pattern"""
        if v not in ALLOWED_SCHEDULED_TASK_ACTIONS:
            raise ValueError(f'Invalid action: {v}. Allowed actions: {", ".join(ALLOWED_SCHEDULED_TASK_ACTIONS)}')
        return v
    
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


class ScheduledTaskUpdate(SQLModel):
    """Schema for updating a scheduled task"""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Task name/description")
    action: Optional[str] = Field(None, description="Action to perform")
    enabled: Optional[bool] = Field(None, description="Whether the task is active")
    schedule_type: Optional[str] = Field(None, description="Schedule type: daily, weekly, interval, cron")
    schedule_value: Optional[str] = Field(None, min_length=1, max_length=255, description="Time or cron expression")
    
    @field_validator('action')
    @classmethod
    def validate_action(cls, v):
        """Validate action matches allowed pattern"""
        if v is not None and v not in ALLOWED_SCHEDULED_TASK_ACTIONS:
            raise ValueError(f'Invalid action: {v}. Allowed actions: {", ".join(ALLOWED_SCHEDULED_TASK_ACTIONS)}')
        return v
    
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


class ScheduledTaskResponse(SQLModel):
    """Schema for scheduled task response"""
    id: int
    server_id: int
    name: str
    action: str
    enabled: bool
    schedule_type: str
    schedule_value: str
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


# GitHub Plugin Installation schemas
class GitHubReleaseAsset(SQLModel):
    """Schema for a GitHub release asset"""
    name: str
    browser_download_url: str
    size: int
    content_type: Optional[str] = None


class GitHubRelease(SQLModel):
    """Schema for a GitHub release"""
    tag_name: str
    name: Optional[str] = None
    published_at: Optional[str] = None
    prerelease: bool = False
    assets: List[GitHubReleaseAsset] = []


class GitHubReleasesResponse(SQLModel):
    """Schema for GitHub releases response"""
    success: bool
    releases: List[GitHubRelease] = []
    error: Optional[str] = None
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None


class ArchiveContentItem(SQLModel):
    """Schema for an item in archive content"""
    path: str
    is_dir: bool
    size: int = 0


class ArchiveAnalysisResponse(SQLModel):
    """Schema for archive content analysis response"""
    success: bool
    has_addons_dir: bool = False
    root_dirs: List[str] = []
    all_dirs: List[str] = []  # All directories in archive (kept for backward compatibility)
    all_files: List[ArchiveContentItem] = []  # All files in archive for exclusion selection
    top_level_items: List[ArchiveContentItem] = []
    archive_type: Optional[str] = None
    error: Optional[str] = None


class GitHubPluginInstallRequest(SQLModel):
    """Schema for GitHub plugin installation request"""
    download_url: str = Field(..., description="Direct download URL for the release asset")
    exclude_dirs: List[str] = Field(default=[], description="Directories to exclude during extraction (deprecated, use exclude_files)")
    exclude_files: List[str] = Field(default=[], description="Files to exclude during extraction (for updates)")
    custom_install_path: Optional[str] = Field(default=None, description="Custom extraction path for non-standard packages (e.g., 'addons')")
    
    @field_validator('download_url')
    @classmethod
    def validate_download_url(cls, v):
        """Validate that URL is from GitHub releases"""
        if not v.startswith('https://github.com/') or '/releases/download/' not in v:
            raise ValueError('Download URL must be a GitHub releases download URL')
        return v
    
    @field_validator('exclude_dirs')
    @classmethod
    def validate_exclude_dirs(cls, v):
        """Validate exclude directories to prevent path traversal"""
        for dir_path in v:
            if '..' in dir_path or dir_path.startswith('/'):
                raise ValueError('Exclude directories cannot contain path traversal sequences')
        return v
    
    @field_validator('exclude_files')
    @classmethod
    def validate_exclude_files(cls, v):
        """Validate exclude files to prevent path traversal"""
        for file_path in v:
            if '..' in file_path or file_path.startswith('/'):
                raise ValueError('Exclude files cannot contain path traversal sequences')
        return v


class GitHubPluginInstallResponse(SQLModel):
    """Schema for GitHub plugin installation response"""
    success: bool
    message: str
    installed_files: int = 0


# Plugin Market schemas
class MarketPluginCreate(SQLModel):
    """Schema for creating a market plugin (admin only)"""
    github_url: str = Field(..., max_length=500, description="GitHub repository URL")
    title: Optional[str] = Field(None, max_length=255, description="Plugin title (auto-filled if not provided)")
    description: Optional[str] = Field(None, description="Plugin description (auto-filled if not provided)")
    author: Optional[str] = Field(None, max_length=255, description="Plugin author")
    version: Optional[str] = Field(None, max_length=50, description="Plugin version")
    category: str = Field(default="other", description="Plugin category")
    tags: Optional[str] = Field(None, description="Comma-separated tags")
    is_recommended: bool = Field(default=False, description="Whether to mark as recommended")
    icon_url: Optional[str] = Field(None, max_length=500, description="Icon URL")
    dependencies: Optional[str] = Field(None, description="Comma-separated plugin IDs")
    custom_install_path: Optional[str] = Field(None, max_length=255, description="Custom extraction path for non-standard packages (e.g., 'addons')")


class MarketPluginUpdate(SQLModel):
    """Schema for updating a market plugin (admin only)"""
    title: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    author: Optional[str] = Field(None, max_length=255)
    version: Optional[str] = Field(None, max_length=50)
    category: Optional[str] = None
    tags: Optional[str] = None
    is_recommended: Optional[bool] = None
    icon_url: Optional[str] = Field(None, max_length=500)
    dependencies: Optional[str] = None
    custom_install_path: Optional[str] = Field(None, max_length=255)


class DependencyInfo(SQLModel):
    """Schema for dependency information"""
    id: int
    title: str


class MarketPluginResponse(SQLModel):
    """Schema for market plugin response"""
    id: int
    github_url: str
    title: str
    description: Optional[str] = None
    author: Optional[str] = None
    version: Optional[str] = None
    category: str
    tags: Optional[str] = None
    is_recommended: bool
    icon_url: Optional[str] = None
    dependencies: Optional[str] = None
    custom_install_path: Optional[str] = None
    dependency_details: Optional[List[DependencyInfo]] = None
    download_count: int
    install_count: int
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


class MarketPluginListResponse(SQLModel):
    """Schema for market plugin list response with pagination"""
    success: bool
    plugins: List[MarketPluginResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class MarketPluginInstallRequest(SQLModel):
    """Schema for installing a market plugin"""
    plugin_id: int = Field(..., description="Market plugin ID to install")
    server_id: int = Field(..., description="Server ID to install plugin on")
    exclude_dirs: List[str] = Field(default=[], description="Directories to exclude from installation")


class GitHubRepoInfo(SQLModel):
    """Schema for GitHub repository information"""
    success: bool
    repo_name: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    error: Optional[str] = None


# Plugin Uninstallation schemas
class PluginUninstallRequest(SQLModel):
    """Schema for plugin uninstallation request"""
    files_to_delete: List[str] = Field(..., description="List of file paths to delete (relative to csgo directory)")
    
    @field_validator('files_to_delete')
    @classmethod
    def validate_files_to_delete(cls, v):
        """Validate file paths to prevent path traversal and injection attacks"""
        import os
        import urllib.parse
        
        for file_path in v:
            # Normalize the path first
            normalized = os.path.normpath(file_path)
            
            # Check for various path traversal attempts
            if '..' in file_path or '..' in normalized:
                raise ValueError('File paths cannot contain path traversal sequences (..)')
            
            # Check for absolute paths
            if file_path.startswith('/') or os.path.isabs(normalized):
                raise ValueError('File paths must be relative (cannot start with /)')
            
            # Check for null bytes
            if '\x00' in file_path:
                raise ValueError('File paths cannot contain null bytes')
            
            # Check for URL-encoded path traversal (specifically look for encoded dots and slashes)
            # Only reject if there are actual encoded path traversal sequences
            decoded = urllib.parse.unquote(file_path)
            if '..' in decoded and '..' not in file_path:
                # Path contains encoded ".." which could be used for traversal
                raise ValueError('File paths cannot contain URL-encoded path traversal sequences')
            
            # Ensure normalized path doesn't escape the base directory
            if normalized.startswith('..') or normalized == '..':
                raise ValueError('Normalized path cannot escape base directory')
        
        return v


class PluginUninstallResponse(SQLModel):
    """Schema for plugin uninstallation response"""
    success: bool
    message: str
    deleted_files: int = 0
    failed_files: List[str] = []


class InstalledPluginFile(SQLModel):
    """Schema for an installed plugin file"""
    path: str
    size: int = 0
    is_dir: bool = False


class InstalledPluginAnalysisResponse(SQLModel):
    """Schema for analyzing installed plugins"""
    success: bool
    files: List[InstalledPluginFile] = []
    total_size: int = 0
    error: Optional[str] = None


# Metamod Detection schemas
class MetamodStatusResponse(SQLModel):
    """Schema for metamod installation status"""
    success: bool
    installed: bool
    path: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


# System Settings Schemas
class SystemSettingsResponse(SQLModel):
    """Schema for system settings response"""
    id: int
    default_proxy_mode: str
    github_proxy_url: Optional[str]
    email_enabled: bool
    email_provider: str
    email_from_address: Optional[str]
    email_from_name: Optional[str]
    smtp_host: Optional[str]
    smtp_port: Optional[int]
    smtp_username: Optional[str]
    smtp_use_tls: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class SystemSettingsUpdate(SQLModel):
    """Schema for updating system settings"""
    default_proxy_mode: Optional[str] = None
    github_proxy_url: Optional[str] = None
    email_enabled: Optional[bool] = None
    email_provider: Optional[str] = None
    email_from_address: Optional[str] = None
    email_from_name: Optional[str] = None
    gmail_credentials_json: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: Optional[bool] = None
    
    @field_validator('default_proxy_mode')
    @classmethod
    def validate_proxy_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ('direct', 'panel', 'github_url'):
            raise ValueError('default_proxy_mode must be one of: direct, panel, github_url')
        return v
    
    @field_validator('email_provider')
    @classmethod
    def validate_email_provider(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ('gmail', 'smtp'):
            raise ValueError('email_provider must be one of: gmail, smtp')
        return v


# Password Reset Schemas
class ForgotPasswordRequest(SQLModel):
    """Schema for forgot password request"""
    email: EmailStr
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")


class ResetPasswordRequest(SQLModel):
    """Schema for reset password request"""
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=100)


class GmailCredentialsUploadRequest(SQLModel):
    """Schema for Gmail OAuth credentials upload"""
    credentials_json: str = Field(..., min_length=1, description="The contents of the credentials.json file from Google Cloud Console")


class EmailTestRequest(SQLModel):
    """Schema for email test request"""
    test_email: EmailStr = Field(..., description="Email address to send test email to")


class DiscordSettingsResponse(SQLModel):
    """Schema for Discord settings without exposing the webhook URL"""
    discord_notifications_enabled: bool
    discord_channel_name: Optional[str] = None
    webhook_configured: bool
    discord_notify_auto_updates: bool
    discord_notify_manual_updates: bool
    discord_notify_plugin_updates: bool
    discord_notify_s3_backups: bool


class DiscordSettingsUpdate(SQLModel):
    """Schema for updating per-server Discord notification settings"""
    discord_notifications_enabled: Optional[bool] = None
    discord_webhook_url: Optional[str] = Field(default=None, max_length=1000)
    discord_channel_name: Optional[str] = Field(default=None, max_length=255)
    discord_notify_auto_updates: Optional[bool] = None
    discord_notify_manual_updates: Optional[bool] = None
    discord_notify_plugin_updates: Optional[bool] = None
    discord_notify_s3_backups: Optional[bool] = None
    clear_webhook: bool = False

    @field_validator('discord_channel_name')
    @classmethod
    def normalize_channel_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        return v or None

    @field_validator('discord_webhook_url')
    @classmethod
    def normalize_webhook_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        return v or None


class DiscordTestRequest(SQLModel):
    """Schema for sending a Discord test notification"""
    message: Optional[str] = Field(default=None, max_length=500)


class GoogleOAuthRequest(SQLModel):
    """Schema for Google OAuth login/register"""
    id_token: str = Field(..., min_length=1, description="Google ID token from frontend")
    username: Optional[str] = Field(default=None, min_length=3, max_length=100, description="Username for new account (if registering)")
    password: Optional[str] = Field(default=None, min_length=6, max_length=100, description="Password for new account (if registering)")
