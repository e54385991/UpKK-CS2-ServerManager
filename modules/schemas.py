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
    "update_metamod", "update_counterstrikesharp", "update_cs2fixes"
]
SERVER_ACTION_PATTERN = f"^({'|'.join(ALLOWED_SERVER_ACTIONS)})$"


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
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(..., min_length=4, max_length=4, description="User-entered CAPTCHA code")


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
