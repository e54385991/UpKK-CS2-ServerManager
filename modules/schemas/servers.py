"""Servers schemas."""

# ruff: noqa: F403,F405

from .auth import UserResponse
from .common import *


class ServerCreate(SQLModel):
    """Schema for creating a new server (password authentication only)"""

    name: str = Field(..., min_length=1, max_length=255)
    host: str = Field(..., min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(..., min_length=1, max_length=100)
    ssh_password: str = Field(..., min_length=1, description="SSH password (required)")
    sudo_password: Optional[str] = None
    ssh_host_key_algorithm: Optional[str] = Field(None, min_length=1, max_length=64)
    ssh_host_key_fingerprint: Optional[str] = Field(None, min_length=16, max_length=128)
    ssh_host_key_confirmed: bool = Field(
        default=False,
        description="User explicitly confirmed the freshly scanned SSH host key",
    )
    game_port: int = Field(default=27015, ge=1, le=65535)
    game_directory: str = Field(default="/home/cs2server/cs2")
    description: Optional[str] = None

    # CAPTCHA validation
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(
        ..., min_length=4, max_length=4, description="User-entered CAPTCHA code"
    )

    # LGSM-style server configuration
    server_name: str = Field(default="CS2 Server", max_length=255)
    server_password: Optional[str] = None
    rcon_password: Optional[str] = None
    steam_account_token: Optional[str] = Field(
        None, max_length=255, description="Steam game server login token (GSLT)"
    )
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
    backend_url: Optional[str] = Field(
        None, max_length=500, description="Backend URL for status reporting (optional)"
    )

    # Auto-cleanup configuration
    auto_clear_crash_hours: Optional[int] = Field(
        None,
        ge=0,
        description="Hours offline before auto-clearing crash history (0 or None = disabled, default 2 hours recommended)",
    )

    # Web-based monitoring configuration
    enable_panel_monitoring: bool = Field(
        default=False, description="Enable web panel monitoring and auto-restart"
    )
    monitor_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=3600,
        description="How often to check server status in seconds (10-3600)",
    )
    auto_restart_on_crash: bool = Field(
        default=True, description="Auto-restart if process not found (when monitoring enabled)"
    )

    # A2S query configuration
    a2s_query_host: Optional[str] = Field(
        None, max_length=255, description="A2S query host (defaults to server host if not set)"
    )
    a2s_query_port: Optional[int] = Field(
        None, ge=1, le=65535, description="A2S query port (defaults to game port if not set)"
    )
    enable_a2s_monitoring: bool = Field(default=False, description="Enable A2S query monitoring")
    a2s_failure_threshold: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of consecutive A2S failures before restart (1-10)",
    )
    a2s_check_interval_seconds: int = Field(
        default=60, ge=15, le=3600, description="A2S check interval in seconds (15-3600)"
    )

    # Auto-update configuration
    current_game_version: Optional[str] = Field(
        None, max_length=50, description="Current installed CS2 version"
    )
    enable_auto_update: bool = Field(
        default=True, description="Enable automatic updates based on Steam API version check"
    )
    update_check_interval_hours: float = Field(
        default=1.0,
        ge=0.0167,
        le=24.0,
        description="Hours between version checks (0.0167-24, where 0.0167≈1 minute)",
    )
    enable_plugin_auto_update: bool = Field(default=False)
    plugin_update_check_interval_hours: float = Field(default=1.0, ge=0.0167, le=24.0)

    # CPU affinity configuration
    cpu_affinity: Optional[str] = Field(
        None,
        max_length=500,
        description="Comma-separated list of CPU cores (e.g., '0,1,2,3' or '0-3,8-11')",
    )

    # Detached console session manager
    session_manager: Literal["screen", "tmux"] = Field(
        default="tmux",
        description="Terminal multiplexer used to run and control the CS2 process",
    )

    # GitHub proxy configuration
    github_proxy: Optional[str] = Field(
        None,
        max_length=500,
        description="GitHub proxy URL (e.g., https://ghfast.top/https://github.com)",
    )

    # Panel proxy mode (mutually exclusive with github_proxy)
    use_panel_proxy: bool = Field(
        default=False,
        description="Use panel server as proxy for all downloads (SteamCMD, GitHub). Mutually exclusive with github_proxy.",
    )

    @field_validator("cpu_affinity")
    @classmethod
    def validate_cpu_affinity(cls, v):
        """Validate CPU affinity format to prevent command injection"""
        if v is None or v.strip() == "":
            return v
        # Only allow digits, commas, and hyphens
        if not re.match(r"^[\d,\-\s]+$", v):
            raise ValueError("CPU affinity must only contain digits, commas, and hyphens")
        return v.strip()

    @field_validator("steam_account_token")
    @classmethod
    def validate_steam_account_token(cls, v):
        """Validate Steam account token format to prevent command injection"""
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        # Steam GSLT tokens are alphanumeric with no special characters that could cause shell injection
        if not re.match(r"^[A-Za-z0-9]+$", v):
            raise ValueError("Steam account token must only contain alphanumeric characters")
        return v

    @model_validator(mode="after")
    def validate_proxy_mutual_exclusivity(self):
        """Ensure github_proxy and use_panel_proxy are mutually exclusive"""
        if self.github_proxy and self.use_panel_proxy:
            raise ValueError(
                "github_proxy and use_panel_proxy are mutually exclusive. Please choose only one."
            )
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
    steam_account_token: Optional[str] = Field(
        None, max_length=255, description="Steam game server login token (GSLT)"
    )
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
    backend_url: Optional[str] = Field(
        None, max_length=500, description="Backend URL for status reporting (optional)"
    )

    # Auto-cleanup configuration
    auto_clear_crash_hours: Optional[int] = Field(
        None,
        ge=0,
        description="Hours offline before auto-clearing crash history (0 or None = disabled)",
    )

    # Web-based monitoring configuration
    enable_panel_monitoring: Optional[bool] = Field(
        None, description="Enable web panel monitoring and auto-restart"
    )
    monitor_interval_seconds: Optional[int] = Field(
        None, ge=10, le=3600, description="How often to check server status in seconds"
    )
    auto_restart_on_crash: Optional[bool] = Field(
        None, description="Auto-restart if process not found"
    )

    # A2S query configuration
    a2s_query_host: Optional[str] = Field(
        None, max_length=255, description="A2S query host (defaults to server host if not set)"
    )
    a2s_query_port: Optional[int] = Field(
        None, ge=1, le=65535, description="A2S query port (defaults to game port if not set)"
    )
    enable_a2s_monitoring: Optional[bool] = Field(None, description="Enable A2S query monitoring")
    a2s_failure_threshold: Optional[int] = Field(
        None, ge=1, le=10, description="Number of consecutive A2S failures before restart"
    )
    a2s_check_interval_seconds: Optional[int] = Field(
        None, ge=15, le=3600, description="A2S check interval in seconds (15-3600)"
    )

    # Auto-update configuration
    current_game_version: Optional[str] = Field(
        None, max_length=50, description="Current installed CS2 version"
    )
    enable_auto_update: Optional[bool] = Field(
        None, description="Enable automatic updates based on Steam API version check"
    )
    update_check_interval_hours: Optional[float] = Field(
        None,
        ge=0.0167,
        le=24.0,
        description="Hours between version checks (0.0167-24, where 0.0167≈1 minute)",
    )
    enable_plugin_auto_update: Optional[bool] = None
    plugin_update_check_interval_hours: Optional[float] = Field(None, ge=0.0167, le=24.0)

    # CPU affinity configuration
    cpu_affinity: Optional[str] = Field(
        None,
        max_length=500,
        description="Comma-separated list of CPU cores (e.g., '0,1,2,3' or '0-3,8-11')",
    )

    # Detached console session manager
    # A default of None makes the PATCH-style field omittable, while the
    # non-optional annotation rejects an explicitly supplied JSON null.
    session_manager: Literal["screen", "tmux"] = Field(
        default=None,
        description="Terminal multiplexer used to run and control the CS2 process",
    )

    # GitHub proxy configuration
    github_proxy: Optional[str] = Field(
        None,
        max_length=500,
        description="GitHub proxy URL (e.g., https://ghfast.top/https://github.com)",
    )

    # Panel proxy mode (mutually exclusive with github_proxy)
    use_panel_proxy: Optional[bool] = Field(
        None,
        description="Use panel server as proxy for all downloads (SteamCMD, GitHub). Mutually exclusive with github_proxy.",
    )

    @field_validator("cpu_affinity")
    @classmethod
    def validate_cpu_affinity(cls, v):
        """Validate CPU affinity format to prevent command injection"""
        if v is None or v.strip() == "":
            return v
        # Only allow digits, commas, and hyphens
        if not re.match(r"^[\d,\-\s]+$", v):
            raise ValueError("CPU affinity must only contain digits, commas, and hyphens")
        return v.strip()

    @field_validator("steam_account_token")
    @classmethod
    def validate_steam_account_token(cls, v):
        """Validate Steam account token format to prevent command injection"""
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        # Steam GSLT tokens are alphanumeric with no special characters that could cause shell injection
        if not re.match(r"^[A-Za-z0-9]+$", v):
            raise ValueError("Steam account token must only contain alphanumeric characters")
        return v

    @model_validator(mode="after")
    def validate_proxy_mutual_exclusivity(self):
        """Ensure github_proxy and use_panel_proxy are mutually exclusive"""
        if self.github_proxy and self.use_panel_proxy:
            raise ValueError(
                "github_proxy and use_panel_proxy are mutually exclusive. Please choose only one."
            )
        return self


class ServerResponse(SQLModel):
    """Secret-free schema for server responses."""

    id: int
    user_id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    ssh_host_key_algorithm: Optional[str] = None
    ssh_host_key_fingerprint: Optional[str] = None
    game_port: int
    game_directory: str
    status: ServerStatus
    description: Optional[str] = None
    last_deployed: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    # LGSM-style server configuration
    server_name: str
    server_password_configured: bool
    rcon_password_configured: bool
    steam_account_token_configured: bool
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
    api_key_configured: bool
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
    enable_plugin_auto_update: bool = False
    plugin_update_check_interval_hours: float = 1.0
    last_plugin_update_check: Optional[datetime] = None

    # CPU affinity configuration
    cpu_affinity: Optional[str] = None

    # Detached console session manager
    session_manager: Literal["screen", "tmux"] = "tmux"

    # GitHub proxy configuration
    github_proxy: Optional[str] = None

    # Panel proxy mode
    use_panel_proxy: bool

    # Restart required flag (set by update endpoint when startup-affecting settings change)
    restart_required: bool = False

    model_config = {"from_attributes": True}


class ServerSummary(ServerResponse):
    """Secret-free list representation.

    During the compatibility phase this intentionally retains the legacy list
    fields.  Giving the representation a dedicated public type lets a later
    version reduce list payloads without conflating list and detail contracts.
    """


class ServerDetail(ServerResponse):
    """Secret-free detailed server representation."""


class ServerCreatedResponse(ServerDetail):
    """Server response which reveals its agent API key exactly once at creation."""

    api_key: str


class SSHHostKeyScanRequest(SQLModel):
    """Inspect a host key without sending SSH credentials."""

    host: str = Field(..., min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)


class SSHHostKeyConfirmRequest(SQLModel):
    """Host key identity a user has explicitly chosen to trust."""

    algorithm: str = Field(..., min_length=1, max_length=64)
    fingerprint: str = Field(..., min_length=16, max_length=128)


class SSHHostKeyResponse(SQLModel):
    """Public SSH host-key metadata and pin state."""

    algorithm: str
    fingerprint: str
    configured: bool = False
    matches_configured: Optional[bool] = None


class ServerResponseWithUser(ServerDetail):
    """Schema for server response with user information (admin only)"""

    user: Optional[UserResponse] = None

    model_config = {"from_attributes": True}


class ServerAction(SQLModel):
    """Schema for server actions"""

    action: str = Field(..., description="Server action to perform")

    @field_validator("action")
    @classmethod
    def validate_action(cls, v):
        """Validate action matches allowed pattern"""
        if not re.match(SERVER_ACTION_PATTERN, v):
            raise ValueError(
                f"Invalid action: {v}. Allowed actions: {', '.join(ALLOWED_SERVER_ACTIONS)}"
            )
        return v


class BatchActionRequest(SQLModel):
    """Schema for batch server actions"""

    server_ids: List[int] = Field(
        ...,
        min_length=1,
        max_length=MAX_BATCH_SERVERS,
        description="List of server IDs to perform action on",
    )
    action: str = Field(..., description="Action to perform on all servers")

    _deduplicate_server_ids = field_validator("server_ids")(_unique_server_ids)

    @field_validator("action")
    @classmethod
    def validate_action(cls, v):
        """Validate action matches allowed pattern"""
        if not re.match(BATCH_ACTION_PATTERN, v):
            raise ValueError(
                f"Invalid action: {v}. Allowed actions: {', '.join(ALLOWED_BATCH_ACTIONS)}"
            )
        return v


class BatchInstallPluginsRequest(SQLModel):
    """Schema for batch plugin installation"""

    server_ids: List[int] = Field(
        ...,
        min_length=1,
        max_length=MAX_BATCH_SERVERS,
        description="List of server IDs to install plugins on",
    )
    plugins: List[str] = Field(
        ..., min_length=1, max_length=len(ALLOWED_PLUGINS), description="List of plugins to install"
    )

    _deduplicate_server_ids = field_validator("server_ids")(_unique_server_ids)

    @field_validator("plugins")
    @classmethod
    def validate_plugins(cls, v):
        """Validate plugin names"""
        for plugin in v:
            if plugin not in ALLOWED_PLUGINS:
                raise ValueError(
                    f"Invalid plugin: {plugin}. Allowed plugins: {', '.join(ALLOWED_PLUGINS)}"
                )
        return v


class BatchActionResponse(SQLModel):
    """Schema for batch action response"""

    success: bool
    message: str
    batch_id: str = Field(..., description="Unique batch ID for tracking progress")
    server_count: int = Field(..., description="Number of servers in batch")


class BatchSendCommandRequest(SQLModel):
    """Schema for batch send command to game servers"""

    server_ids: List[int] = Field(
        ...,
        min_length=1,
        max_length=MAX_BATCH_SERVERS,
        description="List of server IDs to send command to",
    )
    command: str = Field(
        ..., min_length=1, max_length=500, description="Command to send to game servers"
    )

    _deduplicate_server_ids = field_validator("server_ids")(_unique_server_ids)

    @field_validator("command")
    @classmethod
    def validate_command(cls, v):
        """Validate command is not empty and trim whitespace"""
        v = v.strip()
        if not v:
            raise ValueError("Command cannot be empty")
        return v


class CustomCommandCreate(SQLModel):
    """Schema for creating a saved quick command"""

    name: str = Field(..., min_length=1, max_length=255)
    target: str = Field(default="host", description="Send target: game_process or host")
    commands: str = Field(..., min_length=1, max_length=20000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v

    @field_validator("target")
    @classmethod
    def validate_target(cls, v):
        v = v.strip()
        if v not in CUSTOM_COMMAND_TARGETS:
            raise ValueError(f"Target must be one of: {', '.join(CUSTOM_COMMAND_TARGETS)}")
        return v

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, v):
        return _validate_custom_command_text(v)


class CustomCommandUpdate(SQLModel):
    """Schema for updating a saved quick command"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    target: Optional[str] = Field(None, description="Send target: game_process or host")
    commands: Optional[str] = Field(None, min_length=1, max_length=20000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v

    @field_validator("target")
    @classmethod
    def validate_target(cls, v):
        if v is None:
            return v
        v = v.strip()
        if v not in CUSTOM_COMMAND_TARGETS:
            raise ValueError(f"Target must be one of: {', '.join(CUSTOM_COMMAND_TARGETS)}")
        return v

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, v):
        if v is None:
            return v
        return _validate_custom_command_text(v)


class CustomCommandExecuteRequest(SQLModel):
    """Schema for one-time custom command execution"""

    target: str = Field(default="host", description="Send target: game_process or host")
    commands: str = Field(..., min_length=1, max_length=20000)

    @field_validator("target")
    @classmethod
    def validate_target(cls, v):
        v = v.strip()
        if v not in CUSTOM_COMMAND_TARGETS:
            raise ValueError(f"Target must be one of: {', '.join(CUSTOM_COMMAND_TARGETS)}")
        return v

    @field_validator("commands")
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
