"""Response schemas for the versioned ``/api/v1`` surface.

These models are the stable, browser-facing projections. They deliberately
exclude every secret held on the underlying ORM models (SSH/RCON passwords,
Steam GSLT, API keys). Detail views expose only operational, non-sensitive
fields; secret mutation happens through dedicated, explicit actions.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from modules.models.servers import ServerStatus
from modules.server_startup import normalize_additional_parameters
from services.apt_mirrors import normalize_apt_mirror

ItemT = TypeVar("ItemT")


class Page(BaseModel, Generic[ItemT]):
    """Unified offset-based pagination container for list endpoints."""

    items: list[ItemT]
    total: int
    limit: int
    offset: int


class ProblemDetail(BaseModel):
    """RFC 9457-style error body used by the versioned API."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None


class SessionUser(BaseModel):
    """The authenticated principal as the console needs it."""

    id: int
    username: str
    email: str | None = None
    is_admin: bool
    is_active: bool


class RegisterRequest(BaseModel):
    """Public self-registration body. CAPTCHA-gated; creates a non-admin member."""

    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=100)
    captcha_token: str = Field(min_length=1)
    captcha_code: str = Field(min_length=4, max_length=4)


class PasswordResetEmailRequest(BaseModel):
    """Public forgot-password body. CAPTCHA-gated; does not reveal whether the email exists."""

    email: EmailStr
    captcha_token: str = Field(min_length=1)
    captcha_code: str = Field(min_length=4, max_length=4)


class PasswordResetCompleteRequest(BaseModel):
    """Public reset-password body. The token is the one-time value from the email link."""

    token: str = Field(min_length=1)
    new_password: str = Field(min_length=6, max_length=100)


class GoogleConfigView(BaseModel):
    """Public Google OAuth client configuration. ``client_id`` is the browser OAuth client."""

    client_id: str = ""
    enabled: bool = False


class GoogleSignInRequest(BaseModel):
    """Google identity-token sign-in. New accounts also send a username and password."""

    id_token: str = Field(min_length=1)
    username: str | None = Field(default=None, min_length=3, max_length=100)
    password: str | None = Field(default=None, min_length=6, max_length=100)


class AuthTokenView(BaseModel):
    """Session token body. The HttpOnly cookie is the browser session; this mirrors login."""

    access_token: str
    token_type: str = "bearer"


class ProfileView(BaseModel):
    """Personal-center projection. Secrets stay write-only except the API key reveal."""

    id: int
    username: str
    email: str | None = None
    is_admin: bool
    is_active: bool
    created_at: datetime | None = None
    steamcmd_max_retries: int = Field(default=20, ge=0, le=100)
    steamcmd_max_retries_default: int = 20
    steamcmd_max_retries_limit: int = 100
    has_steam_api_key: bool = False
    steam_api_key_prefix: str | None = None
    has_github_token: bool = False
    github_token_prefix: str | None = None
    has_api_key: bool = False


class ProfilePatch(BaseModel):
    """Personal-center updates. Sensitive fields are write-only and captcha-gated."""

    steamcmd_max_retries: int | None = Field(default=None, ge=0, le=100)
    email: EmailStr | None = None
    steam_api_key: str | None = Field(default=None, max_length=64)
    clear_steam_api_key: bool = False
    github_token: str | None = Field(default=None, max_length=255)
    clear_github_token: bool = False
    captcha_token: str | None = None
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)

    @field_validator("steam_api_key")
    @classmethod
    def validate_steam_api_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        key = value.strip()
        if not re.match(r"^[A-Fa-f0-9]{32}$", key):
            raise ValueError("Steam API key must be a 32-character hexadecimal string")
        return key

    @field_validator("github_token")
    @classmethod
    def validate_github_token(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        token = value.strip()
        if not re.match(r"^(github_pat_[A-Za-z0-9_]+|gh[poushr]_[A-Za-z0-9_]+)$", token):
            raise ValueError("GitHub token must be a valid Fine-grained or Classic token")
        return token


class ProfilePasswordChange(BaseModel):
    """Change the signed-in user's password. Requires the current password and captcha."""

    current_password: str = Field(min_length=6, max_length=100)
    new_password: str = Field(min_length=6, max_length=100)
    confirm_password: str = Field(min_length=6, max_length=100)
    captcha_token: str
    captcha_code: str = Field(min_length=4, max_length=4)


class ProfileApiKeyView(BaseModel):
    """The user's own panel API key. Returned only on this dedicated reveal endpoint."""

    api_key: str
    created_at: datetime | None = None


class ProfileApiKeyGenerate(BaseModel):
    """Optional captcha when rotating the personal API key from the console."""

    captcha_token: str | None = None
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)


class ProfileGsltGenerate(BaseModel):
    """Create a Steam GSLT with the signed-in user's Steam Web API key."""

    server_name: str | None = Field(default=None, max_length=255)
    captcha_token: str
    captcha_code: str = Field(min_length=4, max_length=4)


class ProfileGsltView(BaseModel):
    """Newly generated GSLT. Returned once so the operator can save it on a server."""

    login_token: str
    steamid: str | None = None


class ProfileS3View(BaseModel):
    """S3-compatible backup settings. The secret key is never echoed."""

    enabled: bool
    endpoint_url: str | None = None
    region: str | None = None
    bucket: str | None = None
    access_key_id: str | None = None
    prefix: str | None = None
    use_ssl: bool = True
    retention_count: int = 10
    has_secret: bool = False
    is_configured: bool = False


class ProfileS3Patch(BaseModel):
    """Partial S3 backup update. Secret is write-only; captcha is required."""

    enabled: bool | None = None
    endpoint_url: str | None = Field(default=None, max_length=500)
    region: str | None = Field(default=None, max_length=100)
    bucket: str | None = Field(default=None, max_length=255)
    access_key_id: str | None = Field(default=None, max_length=255)
    secret_access_key: str | None = Field(default=None, max_length=255)
    prefix: str | None = Field(default=None, max_length=255)
    use_ssl: bool | None = None
    retention_count: int | None = Field(default=None, ge=1, le=10000)
    clear_secret: bool = False
    captcha_token: str
    captcha_code: str = Field(min_length=4, max_length=4)

    @field_validator(
        "endpoint_url", "region", "bucket", "access_key_id", "secret_access_key", "prefix"
    )
    @classmethod
    def strip_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("bucket")
    @classmethod
    def validate_bucket(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        if "/" in value or "\\" in value:
            raise ValueError("S3 bucket name cannot contain slashes")
        return value

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        if any(char in value for char in ["\\", "\n", "\r"]):
            raise ValueError("S3 prefix contains invalid characters")
        return value.strip("/")


class ProfileS3TestStep(BaseModel):
    name: str
    status: str
    message: str


class ProfileS3TestView(BaseModel):
    success: bool
    message: str
    steps: list[ProfileS3TestStep] = Field(default_factory=list)


class AssistantUserSettingsView(BaseModel):
    """Personal AI provider. API keys stay write-only."""

    mode: Literal["global", "custom"]
    base_url: str | None = None
    model: str | None = None
    api_protocol: Literal["chat_completions", "responses"]
    api_key_configured: bool
    reasoning_effort: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_completion_tokens: int = 2048
    token_limit_parameter: Literal["max_completion_tokens", "max_tokens", "omit"] = (
        "max_completion_tokens"
    )
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    verbosity: str | None = None
    parallel_tool_calls: bool | None = None
    provider_tested: bool
    tool_calling_tested: bool
    streaming_tested: bool
    effective_enabled: bool
    effective_source: Literal["global", "custom", "none"]


class AssistantUserSettingsPatch(BaseModel):
    mode: Literal["global", "custom"] | None = None
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=255)
    api_protocol: Literal["chat_completions", "responses"] | None = None
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    reasoning_effort: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_completion_tokens: int | None = Field(default=None, ge=256, le=32768)
    token_limit_parameter: Literal["max_completion_tokens", "max_tokens", "omit"] | None = None
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    verbosity: str | None = None
    parallel_tool_calls: bool | None = None


class ServerSummary(BaseModel):
    """Non-secret server projection for list and card views."""

    id: int
    name: str
    host: str
    game_port: int
    ssh_user: str
    status: ServerStatus
    description: str | None = None
    default_map: str
    max_players: int
    owner_id: int | None = None
    owner_username: str | None = None
    owner_is_admin: bool | None = None
    use_panel_proxy: bool = False
    github_proxy: str | None = None
    is_ssh_down: bool = False
    ssh_health_status: str = "unknown"
    consecutive_ssh_failures: int = 0
    ssh_health_failure_threshold: int = 84
    ssh_health_check_interval_hours: int = 2
    last_ssh_health_check: datetime | None = None


class ServerDetail(ServerSummary):
    """Extended, still non-secret, server projection for the workspace."""

    ssh_port: int
    ssh_user: str
    game_directory: str
    game_mode: str
    game_type: str
    server_name: str
    session_manager: Literal["screen", "tmux"] = "tmux"
    enable_panel_monitoring: bool = False
    monitor_interval_seconds: int = 60
    auto_restart_on_crash: bool = True
    enable_a2s_monitoring: bool = False
    a2s_failure_threshold: int = 3
    a2s_check_interval_seconds: int = 60
    a2s_query_host: str | None = None
    a2s_query_port: int | None = None
    enable_auto_update: bool = True
    tv_enable: bool = False
    is_ssh_down: bool = False
    last_ssh_success: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_deployed: datetime | None = None
    apt_mirror: str | None = None
    additional_parameters: str | None = None
    has_sudo_password: bool = False
    ssh_pooled: bool = False
    ssh_in_use: bool = False
    ssh_active_leases: int = 0
    ssh_idle_seconds: float | None = None


class ServerWriteResult(ServerDetail):
    """Detail plus whether a running server needs a restart after the write."""

    restart_required: bool = False


class ServerCreateResult(ServerDetail):
    """Create response: the server plus host-initialization outcome."""

    host_initialized: bool = True
    missing_packages: list[str] = Field(default_factory=list)
    manual_install_command: str | None = None
    initialization_message: str = ""


class ServerUpdateRequest(BaseModel):
    """Partial server update. Secrets are write-only; omit to leave unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = Field(default=None, min_length=1, max_length=100)
    ssh_password: str | None = Field(default=None, max_length=255)
    game_port: int | None = Field(default=None, ge=1, le=65535)
    game_directory: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    server_name: str | None = Field(default=None, max_length=255)
    default_map: str | None = Field(default=None, max_length=100)
    max_players: int | None = Field(default=None, ge=1, le=64)
    game_mode: str | None = Field(default=None, max_length=50)
    game_type: str | None = Field(default=None, max_length=50)
    session_manager: Literal["screen", "tmux"] | None = None
    enable_panel_monitoring: bool | None = None
    monitor_interval_seconds: int | None = Field(default=None, ge=10, le=3600)
    auto_restart_on_crash: bool | None = None
    enable_a2s_monitoring: bool | None = None
    a2s_failure_threshold: int | None = Field(default=None, ge=1, le=10)
    a2s_check_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    a2s_query_host: str | None = Field(default=None, max_length=255)
    a2s_query_port: int | None = Field(default=None, ge=1, le=65535)
    enable_auto_update: bool | None = None
    tv_enable: bool | None = None
    rcon_password: str | None = Field(default=None, max_length=255)
    steam_account_token: str | None = Field(default=None, max_length=255)
    sudo_password: str | None = Field(default=None, max_length=255)
    apt_mirror: str | None = Field(default=None, max_length=32)
    use_panel_proxy: bool | None = None
    github_proxy: str | None = Field(default=None, max_length=500)
    additional_parameters: str | None = Field(default=None, max_length=4096)

    @field_validator("ssh_password", "rcon_password", "description", "sudo_password")
    @classmethod
    def empty_optional_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("steam_account_token")
    @classmethod
    def validate_steam_account_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip()
        if not token:
            return None
        if not re.match(r"^[A-Za-z0-9]+$", token):
            raise ValueError("Steam account token must only contain alphanumeric characters")
        return token

    @field_validator("additional_parameters")
    @classmethod
    def validate_additional_parameters(cls, value: str | None) -> str | None:
        return normalize_additional_parameters(value)

    @field_validator("apt_mirror")
    @classmethod
    def validate_apt_mirror(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        normalized = normalize_apt_mirror(stripped)
        if normalized is None:
            raise ValueError("apt_mirror must be official, ustc, or tuna/tsinghua")
        return normalized

    @field_validator("github_proxy", "a2s_query_host")
    @classmethod
    def empty_github_proxy_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def validate_proxy_mutual_exclusivity(self) -> ServerUpdateRequest:
        if self.use_panel_proxy and self.github_proxy:
            raise ValueError(
                "github_proxy and use_panel_proxy are mutually exclusive. Please choose only one."
            )
        return self


class ServerCreateRequest(BaseModel):
    """Create a server. Secret fields are write-only and never echoed."""

    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=100)
    ssh_password: str = Field(min_length=1, max_length=255)
    sudo_password: str | None = Field(default=None, max_length=255)
    apt_mirror: str | None = Field(default=None, max_length=32)
    game_port: int = Field(default=27015, ge=1, le=65535)
    game_directory: str = Field(default="/home/cs2server/cs2", min_length=1, max_length=500)
    description: str | None = None
    captcha_token: str = Field(min_length=1)
    captcha_code: str = Field(min_length=4, max_length=4)
    server_name: str = Field(default="CS2 Server", max_length=255)
    default_map: str = Field(default="de_dust2", max_length=100)
    max_players: int = Field(default=32, ge=1, le=64)
    game_mode: str = Field(default="competitive", max_length=50)
    game_type: str = Field(default="0", max_length=50)
    rcon_password: str | None = Field(default=None, max_length=255)
    steam_account_token: str | None = Field(default=None, max_length=255)
    additional_parameters: str | None = Field(default=None, max_length=4096)
    session_manager: Literal["screen", "tmux"] = "tmux"

    @field_validator("sudo_password", "description", "rcon_password")
    @classmethod
    def empty_secret_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("steam_account_token")
    @classmethod
    def validate_steam_account_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip()
        if not token:
            return None
        if not re.match(r"^[A-Za-z0-9]+$", token):
            raise ValueError("Steam account token must only contain alphanumeric characters")
        return token

    @field_validator("additional_parameters")
    @classmethod
    def validate_additional_parameters(cls, value: str | None) -> str | None:
        return normalize_additional_parameters(value)

    @field_validator("apt_mirror")
    @classmethod
    def validate_apt_mirror(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        normalized = normalize_apt_mirror(stripped)
        if normalized is None:
            raise ValueError("apt_mirror must be official, ustc, or tuna/tsinghua")
        return normalized


class OverviewSummary(BaseModel):
    """Aggregate operational counters for the overview dashboard."""

    total: int
    running: int
    attention: int
    capacity: int
    ssh_connections: int = 0
    ssh_in_use: int = 0
    ssh_idle: int = 0
    ssh_leases: int = 0


class SteamLatestVersionView(BaseModel):
    """Cached Steam CS2 advertised version. Never queries Steam on this request."""

    available: bool = False
    version: str | None = None
    message: str | None = None
    timestamp: datetime | None = None


class DiskSpaceView(BaseModel):
    """Cached host disk snapshot for one game directory. Default reads never SSH."""

    server_id: int
    cached: bool = False
    used_gb: float | None = None
    total_gb: float | None = None
    available_gb: float | None = None
    used_percent: float | None = None


class DiskSpaceListView(BaseModel):
    servers: list[DiskSpaceView] = Field(default_factory=list)
    timestamp: datetime


class A2SCacheView(BaseModel):
    """Cached A2S snapshot for one server. Default reads never query A2S or SSH."""

    server_id: int
    cached: bool = False
    success: bool | None = None
    player_count: int | None = None
    max_players: int | None = None
    map_name: str | None = None
    server_name: str | None = None
    version: str | None = None
    last_updated: datetime | None = None
    response_time_ms: int | None = None


class A2SCacheListView(BaseModel):
    servers: list[A2SCacheView] = Field(default_factory=list)
    timestamp: datetime


class A2SServerInfoView(BaseModel):
    """One A2S_INFO payload. Extra Valve fields are ignored."""

    server_name: str | None = None
    map_name: str | None = None
    folder: str | None = None
    game: str | None = None
    player_count: int | None = None
    max_players: int | None = None
    bot_count: int | None = None
    server_type: str | None = None
    platform: str | None = None
    password_protected: bool | None = None
    vac_enabled: bool | None = None
    version: str | None = None
    ping: float | None = None
    keywords: str | None = None
    game_id: int | None = None


class A2SPlayerView(BaseModel):
    name: str = ""
    score: int = 0
    duration: float = 0


class A2SQueryView(BaseModel):
    """Last cached A2S snapshot, or a live query when requested."""

    query_host: str
    query_port: int
    success: bool
    cached: bool = False
    live: bool = False
    server_info: A2SServerInfoView | None = None
    players: list[A2SPlayerView] = Field(default_factory=list)
    timestamp: datetime | None = None
    last_updated: datetime | None = None
    response_time_ms: int | None = None
    error: str | None = None


class MonitoringLogView(BaseModel):
    id: str
    event_type: str
    status: str
    message: str
    created_at: datetime | None = None


class MonitoringLogListView(BaseModel):
    items: list[MonitoringLogView] = Field(default_factory=list)


class SshPoolView(BaseModel):
    """Non-secret SSH connection-pool snapshot for the console chrome."""

    connections: int = 0
    in_use: int = 0
    idle: int = 0
    leases: int = 0
    draining: int = 0
    idle_timeout: int = 900
    max_lifetime: int = 3600
    keepalive_interval: int = 30
    keepalive_count_max: int = 3


class AuditEntry(BaseModel):
    """One administrator-visible audit event (metadata only, non-secret)."""

    id: str
    created_at: datetime | None = None
    category: str
    action: str
    status: str
    actor_username: str | None = None
    ip_address: str | None = None
    source: str
    server_id: int | None = None
    details: dict = {}


ProxyMode = Literal["direct", "panel", "github_url"]
EmailProvider = Literal["gmail", "smtp"]


class SystemSettingsView(BaseModel):
    """Admin system settings with secrets replaced by presence flags."""

    default_proxy_mode: ProxyMode
    github_proxy_url: str | None = None
    has_global_github_token: bool
    global_github_token_prefix: str | None = None
    email_enabled: bool
    email_provider: EmailProvider
    email_from_address: str | None = None
    email_from_name: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_use_tls: bool
    has_smtp_password: bool
    has_gmail_credentials: bool
    has_gmail_token: bool
    gmail_ready: bool
    updated_at: datetime | None = None


class SystemSettingsPatch(BaseModel):
    """Partial admin update. Secret fields are write-only and never echoed."""

    default_proxy_mode: ProxyMode | None = None
    github_proxy_url: str | None = None
    global_github_token: str | None = Field(default=None, max_length=255)
    clear_global_github_token: bool = False
    email_enabled: bool | None = None
    email_provider: EmailProvider | None = None
    email_from_address: str | None = None
    email_from_name: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = Field(default=None, max_length=255)
    smtp_use_tls: bool | None = None

    @field_validator("global_github_token")
    @classmethod
    def validate_global_github_token(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        token = value.strip()
        if not re.match(r"^(github_pat_[A-Za-z0-9_]+|gh[poushr]_[A-Za-z0-9_]+)$", token):
            raise ValueError("Global GitHub token must be a valid Fine-grained or Classic token")
        return token

    @field_validator(
        "github_proxy_url", "email_from_address", "email_from_name", "smtp_host", "smtp_username"
    )
    @classmethod
    def empty_string_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class EmailTestRequest(BaseModel):
    """Send a test message through the currently saved email configuration."""

    test_email: EmailStr


class EmailTestResult(BaseModel):
    success: bool
    message: str


class GmailCredentialsUpload(BaseModel):
    """Write-only Google Cloud OAuth client JSON for Gmail API."""

    credentials_json: str = Field(min_length=1)


class GmailAuthorizeResult(BaseModel):
    authorization_url: str
    state: str | None = None


class ActionResult(BaseModel):
    success: bool
    message: str


ServerLifecycleAction = Literal[
    "deploy",
    "start",
    "stop",
    "restart",
    "status",
    "update",
    "validate",
    "install_metamod",
    "install_counterstrikesharp",
    "install_cs2fixes",
    "install_swiftly",
    "update_metamod",
    "update_counterstrikesharp",
    "update_cs2fixes",
    "update_swiftly",
    "backup_plugins",
]

ServerOperationAction = Literal[
    "deploy",
    "start",
    "stop",
    "restart",
    "status",
    "update",
    "validate",
    "install_metamod",
    "install_counterstrikesharp",
    "install_cs2fixes",
    "install_swiftly",
    "update_metamod",
    "update_counterstrikesharp",
    "update_cs2fixes",
    "update_swiftly",
    "backup_plugins",
    "install_plugin",
    "install_github_plugin",
    "uninstall_github_plugin",
    "apply_apt_mirror",
    "s3_restore",
    "install_game_mode",
    "extract_archive",
    "download_url",
    "cleanup_delete",
    "cleanup_system",
]

ServerOperationStatus = Literal["queued", "running", "completed", "failed"]


class S3BackupItemView(BaseModel):
    """One S3 plugin-backup object. The object key is a path, not an access key."""

    key: str
    filename: str
    size: int
    last_modified: datetime | None = None


class S3BackupListView(BaseModel):
    """S3 backup listing. Credentials never appear; an empty list is valid when unconfigured."""

    configured: bool
    items: list[S3BackupItemView]
    message: str | None = None


class S3RestoreBody(BaseModel):
    """Restore one listed backup. The object key must belong to this server's prefix."""

    object_key: str = Field(min_length=1, max_length=1024)

    @field_validator("object_key")
    @classmethod
    def validate_object_key(cls, value: str) -> str:
        key = value.strip()
        if not key or key.startswith("/") or "\\" in key:
            raise ValueError("Invalid S3 object key")
        if any(char in key for char in ["\n", "\r", "\x00"]):
            raise ValueError("S3 object key contains invalid characters")
        return key


class AptMirrorApplyRequest(BaseModel):
    """Switch the host apt mirror and retry privileged package install."""

    mirror: str = Field(min_length=1, max_length=32)

    @field_validator("mirror")
    @classmethod
    def validate_mirror(cls, value: str) -> str:
        normalized = normalize_apt_mirror(value)
        if normalized is None:
            raise ValueError("mirror must be official, ustc, or tuna/tsinghua")
        return normalized


class ServerOperationRequest(BaseModel):
    """Start a long-running server action. The HTTP request returns immediately."""

    action: ServerLifecycleAction


class ServerOperationView(BaseModel):
    """Non-secret projection of one async server operation."""

    operation_id: str
    server_id: int
    action: ServerOperationAction
    status: ServerOperationStatus
    success: bool | None = None
    message: str | None = None
    server_status: ServerStatus | None = None
    started_at: datetime
    completed_at: datetime | None = None
    actor_user_id: int
    stream_url: str
    command: str | None = None


class OperationJournalEvent(BaseModel):
    """One persisted operation log line for JSON replay (SSE fallback)."""

    sequence: str
    operation_id: str
    type: str
    kind: str
    message: str
    timestamp: str
    success: bool | None = None
    server_status: str | None = None


class OperationJournal(BaseModel):
    """Current operation record plus every persisted progress event."""

    operation: ServerOperationView
    events: list[OperationJournalEvent] = Field(default_factory=list)


class CurrentServerOperation(BaseModel):
    operation: ServerOperationView | None = None


class OperationInboxItem(ServerOperationView):
    server_name: str
    latest_message: str | None = None
    queue_position: int = 0


class OperationInboxView(BaseModel):
    items: list[OperationInboxItem] = Field(default_factory=list)
    failed_items: list[OperationInboxItem] = Field(default_factory=list)
    active_count: int = 0
    running_count: int = 0
    failed_count: int = 0
    failed_retention_days: int = 7


class DeploymentLockView(BaseModel):
    lock_active: bool
    server_status: ServerStatus


class DeploymentLogEntry(BaseModel):
    """Recent operation history. Output is redacted and truncated."""

    id: int
    action: str
    status: str
    output: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None


class PluginRef(BaseModel):
    id: int
    title: str


class MarketPluginView(BaseModel):
    """Non-secret marketplace listing. GitHub URLs are public repository links."""

    id: int
    title: str
    description: str | None = None
    author: str | None = None
    version: str | None = None
    category: str
    tags: str | None = None
    is_recommended: bool
    icon_url: str | None = None
    github_url: str
    download_count: int
    install_count: int
    dependencies: list[PluginRef] = Field(default_factory=list)


class PluginCategoryView(BaseModel):
    value: str
    name: str


class PluginCategoryList(BaseModel):
    items: list[PluginCategoryView]


class ManagedPluginView(BaseModel):
    """A plugin already tracked on one game server."""

    id: int
    server_id: int
    source_type: str
    source_key: str
    display_name: str
    repo_url: str | None = None
    market_plugin_id: int | None = None
    framework_key: str | None = None
    installed_version: str
    latest_version: str | None = None
    auto_update_enabled: bool
    last_status: str | None = None
    last_error: str | None = None
    last_check_at: datetime | None = None
    last_update_at: datetime | None = None


class ManagedPluginUpdateView(ManagedPluginView):
    """Managed plugin plus auto-update exclusion paths."""

    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)
    backup_before_update: bool = False
    restart_after_update: bool = False


class PluginConflictView(BaseModel):
    rule_id: int
    plugin_a_id: int
    plugin_b_id: int
    severity: str
    reason: str


class PluginInstallStep(BaseModel):
    order: int
    plugin_id: int
    title: str
    kind: str
    status: str
    reason: str


class PluginInstallPlanView(BaseModel):
    """Deterministic install preflight. Does not mutate the server."""

    server_id: int
    plugin: PluginRef
    dependencies: list[PluginRef] = Field(default_factory=list)
    installation_order: list[int] = Field(default_factory=list)
    already_installed: list[int] = Field(default_factory=list)
    tracking_records_without_remote_evidence: list[str] = Field(default_factory=list)
    compatibility_unknown: list[str] = Field(default_factory=list)
    hard_conflicts: list[PluginConflictView] = Field(default_factory=list)
    warnings: list[PluginConflictView] = Field(default_factory=list)
    steps: list[PluginInstallStep] = Field(default_factory=list)
    blocked: bool
    plan_hash: str


class PluginInstallRequest(BaseModel):
    """Acknowledge warnings and optionally pin the preflight plan hash.

    ``install_dependencies`` is opt-in, matching the legacy web installer.
    """

    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)
    plan_hash: str | None = Field(default=None, max_length=64)
    download_url: str | None = Field(default=None, max_length=2000)
    upgrade_mode: bool = False
    install_dependencies: bool = False
    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)

    @field_validator("download_url")
    @classmethod
    def validate_market_download_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if not text.startswith("https://github.com/") or "/releases/download/" not in text:
            raise ValueError("download_url must be a GitHub releases download URL")
        return text

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_market_exclusions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if ".." in text.split("/") or text.startswith("/") or "\x00" in text:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
            cleaned.append(text)
        return cleaned


class LinuxRuntimeProfileView(BaseModel):
    distro_id: str | None = None
    distro_version: str | None = None
    pretty_name: str | None = None
    glibc_version: str | None = None
    recommended_steam_runtime: str | None = None
    detection_source: str = "unknown"
    reason: str = ""


class GitHubReleaseAssetView(BaseModel):
    name: str
    browser_download_url: str
    size: int = 0
    content_type: str | None = None
    steam_runtime: str | None = None
    runtime_compatibility: str = "not_applicable"


class GitHubReleaseView(BaseModel):
    id: str | None = None
    tag_name: str
    name: str | None = None
    published_at: str | None = None
    prerelease: bool = False
    assets: list[GitHubReleaseAssetView] = Field(default_factory=list)


class GitHubReleasesView(BaseModel):
    repo_owner: str | None = None
    repo_name: str | None = None
    releases: list[GitHubReleaseView] = Field(default_factory=list)
    linux_runtime_profile: LinuxRuntimeProfileView | None = None


class ArchiveFileView(BaseModel):
    path: str
    is_dir: bool = False
    size: int = 0


class GitHubArchiveView(BaseModel):
    has_addons_dir: bool = False
    root_dirs: list[str] = Field(default_factory=list)
    all_dirs: list[str] = Field(default_factory=list)
    all_files: list[ArchiveFileView] = Field(default_factory=list)
    archive_type: str | None = None


class ArchiveMappingView(BaseModel):
    source: str
    target: str


class GitHubInstallPlanRequest(BaseModel):
    repo_url: str = Field(min_length=1, max_length=500)
    mode: Literal["install", "upgrade"] = "install"
    asset_name: str | None = Field(default=None, max_length=500)
    config_policy: Literal["preserve", "overwrite"] = "preserve"
    recipe_id: int | None = Field(default=None, gt=0)
    source_prefix: str | None = Field(default=None, max_length=500)
    target_prefix: str | None = Field(default=None, max_length=500)
    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)

    @field_validator("repo_url")
    @classmethod
    def validate_github_repo_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not re.match(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", text):
            raise ValueError("repo_url must be a GitHub repository URL")
        return text

    @field_validator("source_prefix", "target_prefix")
    @classmethod
    def validate_mapping_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.replace("\\", "/").strip()
        if not text or text == ".":
            return None
        if text.startswith("/") or ".." in text.split("/") or "\x00" in text:
            raise ValueError("mapping prefix must stay inside the archive")
        return text.strip("/")

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_plan_exclusions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if ".." in text.split("/") or text.startswith("/") or "\x00" in text:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
            cleaned.append(text)
        return cleaned


class GitHubInstallRequest(GitHubInstallPlanRequest):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)
    acknowledge_unknown_compatibility: bool = False


class GitHubUninstallRequest(BaseModel):
    """Delete selected plugin files under the server csgo directory."""

    files_to_delete: list[str] = Field(min_length=1, max_length=500)
    market_plugin_id: int | None = Field(default=None, gt=0)

    @field_validator("files_to_delete")
    @classmethod
    def validate_files_to_delete(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if "\x00" in text:
                raise ValueError("File paths cannot contain null bytes")
            if text.startswith("/"):
                raise ValueError("File paths must be relative (cannot start with /)")
            parts = [part for part in text.split("/") if part not in {"", "."}]
            if not parts or any(part == ".." for part in parts):
                raise ValueError("File paths cannot contain path traversal sequences (..)")
            path = "/".join(parts)
            if path in seen:
                continue
            seen.add(path)
            cleaned.append(path)
        if not cleaned:
            raise ValueError("Select at least one file to delete")
        return cleaned


class GitHubInstallPlanView(BaseModel):
    server_id: int
    repo_url: str
    mode: str
    config_policy: str
    plan_hash: str
    release_tag: str | None = None
    release_name: str | None = None
    asset_name: str | None = None
    archive_sha256: str | None = None
    mapping_required: bool = False
    source_prefix: str | None = None
    mapping: list[ArchiveMappingView] = Field(default_factory=list)
    recipe_id: int | None = None
    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    hard_conflicts: list[PluginConflictView] = Field(default_factory=list)
    conflict_warnings: list[PluginConflictView] = Field(default_factory=list)
    compatibility_unknown: bool = False
    already_installed: list[int] = Field(default_factory=list)
    dependencies: list[PluginRef] = Field(default_factory=list)
    linux_runtime_profile: LinuxRuntimeProfileView | None = None


class BatchActionRequest(BaseModel):
    server_ids: list[int] = Field(min_length=1, max_length=20)
    action: Literal["restart", "stop", "update"]

    @field_validator("server_ids")
    @classmethod
    def unique_server_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class BatchInstallPluginsRequest(BaseModel):
    server_ids: list[int] = Field(min_length=1, max_length=20)
    plugins: list[Literal["metamod", "counterstrikesharp", "cs2fixes"]] = Field(
        min_length=1, max_length=3
    )

    @field_validator("server_ids")
    @classmethod
    def unique_server_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))

    @field_validator("plugins")
    @classmethod
    def unique_plugins(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class BatchSendCommandRequest(BaseModel):
    server_ids: list[int] = Field(min_length=1, max_length=20)
    command: str = Field(min_length=1, max_length=500)

    @field_validator("server_ids")
    @classmethod
    def unique_server_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))

    @field_validator("command")
    @classmethod
    def strip_command(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("command must not be empty")
        return text


class BatchActionView(BaseModel):
    batch_id: str
    action: str
    server_count: int
    accepted_server_ids: list[int] = Field(default_factory=list)
    stream_url: str
    message: str


class BatchServerStatusView(BaseModel):
    server_id: int
    status: str
    message: str = ""


class BatchSummaryView(BaseModel):
    total: int
    completed: int
    succeeded: int
    failed: int
    in_progress: int
    is_complete: bool


class BatchJournalView(BaseModel):
    batch_id: str
    action: str | None = None
    servers: list[BatchServerStatusView] = Field(default_factory=list)
    summary: BatchSummaryView


class MapEntryView(BaseModel):
    """One MapChooser pool entry. Official maps use an empty workshop_id."""

    name: str
    workshop_id: str = ""
    enabled: bool = True
    filename: str = ""
    min_players: str = ""
    only_nominate: bool = False
    restricted_times: str = ""


class MapPluginFieldView(BaseModel):
    key: str
    kind: str
    value: bool | int | float | str
    group: str
    known: bool = True


class MapPluginConfigView(BaseModel):
    revision: str
    file_exists: bool
    fields: list[MapPluginFieldView] = Field(default_factory=list)
    unsupported_fields: list[str] = Field(default_factory=list)
    config_error: str | None = None


class MapSyncView(BaseModel):
    url: str = ""
    enabled: bool = False
    interval_seconds: int = 300
    last_run: datetime | None = None
    next_run: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    run_count: int = 0


class MapsWorkspaceView(BaseModel):
    """MapChooser workspace. GET stays 200 when SSH or prerequisites are down."""

    server_id: int
    ssh_ok: bool
    ssh_error: str | None = None
    ready: bool = False
    counterstrikesharp_installed: bool = False
    mapchooser_installed: bool = False
    maps_file_exists: bool = False
    plugin_config_file_exists: bool = False
    maps_path: str | None = None
    plugin_config_path: str | None = None
    plugin_center_name: str | None = None
    maps: list[MapEntryView] = Field(default_factory=list)
    revision: str | None = None
    config_error: str | None = None
    plugin_config: MapPluginConfigView | None = None
    custom_sync: MapSyncView
    message: str | None = None


class MapAddRequest(BaseModel):
    workshop_id: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    min_players: int = Field(default=0, ge=0, le=64)
    only_nominate: bool = False
    restricted_times: str = Field(default="", max_length=512)


class MapPoolIdentityRequest(BaseModel):
    """Identify a pool entry. Official maps send an empty workshop_id."""

    name: str = Field(min_length=1, max_length=128)
    workshop_id: str = Field(default="", max_length=20)
    expected_revision: str = Field(min_length=64, max_length=64)

    @field_validator("workshop_id")
    @classmethod
    def allow_official_workshop_id(cls, value: str) -> str:
        stripped = (value or "").strip()
        if stripped in {"", "0"}:
            return ""
        if not re.fullmatch(r"[0-9]{1,20}", stripped):
            raise ValueError("workshop_id must be empty or numeric")
        return stripped


class MapEnabledPatchRequest(MapPoolIdentityRequest):
    enabled: bool


class MapPresetApplyRequest(BaseModel):
    preset: Literal["official", "kz", "ze"]
    expected_revision: str = Field(min_length=64, max_length=64)
    plugin_config_expected_revision: str | None = Field(default=None, min_length=64, max_length=64)


class MapSyncUpdateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    interval_seconds: int = Field(default=300, ge=300, le=86400)
    enabled: bool = False


class MapSyncRunRequest(BaseModel):
    expected_revision: str = Field(min_length=64, max_length=64)


class MapPluginConfigUpdateRequest(BaseModel):
    values: dict[str, bool | int | float | str]
    expected_revision: str | None = Field(default=None, min_length=64, max_length=64)


class MapChooserUninstallRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=64)


class PluginConfigSourceView(BaseModel):
    """A persisted plugin-config file or directory under the game root."""

    id: int | None = None
    path: str
    absolute_path: str
    name: str
    type: Literal["file", "directory"]
    is_default: bool = False
    persisted: bool = False


class PluginConfigSourcesView(BaseModel):
    """Source list. GET is database-only and does not open SSH."""

    server_id: int
    game_directory: str
    sources: list[PluginConfigSourceView] = Field(default_factory=list)


class PluginConfigSourceCreateRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1500)


class PluginConfigSourceDeleteResult(BaseModel):
    success: bool = True


class PluginConfigBrowseItemView(BaseModel):
    name: str
    path: str | None = None
    type: Literal["file", "directory", "symlink"]
    selectable: bool = False
    size: int = 0


class PluginConfigBrowseView(BaseModel):
    path: str
    items: list[PluginConfigBrowseItemView] = Field(default_factory=list)


class PluginConfigFieldView(BaseModel):
    id: str
    key: str
    group: str
    kind: str
    value: bool | int | float | str | None = None
    line: int = 0
    comment: str = ""


class PluginConfigFileView(BaseModel):
    path: str
    name: str
    format: str
    revision: str
    content: str
    visual_supported: bool = False
    parse_error: str | None = None
    fields: list[PluginConfigFieldView] = Field(default_factory=list)
    message: str | None = None


class PluginConfigChange(BaseModel):
    id: str = Field(min_length=1, max_length=1500)
    value: bool | int | float | str | None = None


class PluginConfigSaveRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1500)
    expected_revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    mode: Literal["visual", "raw"]
    changes: list[PluginConfigChange] = Field(default_factory=list, max_length=5000)
    content: str | None = Field(default=None, max_length=10 * 1024 * 1024)


class FileEntryView(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory"]
    size: int = 0
    modified: float = 0
    permissions: str = "000"
    is_symlink: bool = False


class FilesWorkspaceView(BaseModel):
    """Directory listing. GET stays 200 when SSH is down."""

    server_id: int
    root: str
    path: str
    ssh_ok: bool
    ssh_error: str | None = None
    files: list[FileEntryView] = Field(default_factory=list)
    message: str | None = None


class FileContentView(BaseModel):
    path: str
    content: str


class FileContentUpdateRequest(BaseModel):
    content: str = Field(max_length=10 * 1024 * 1024)


class FileMkdirRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class FileRenameRequest(BaseModel):
    old_name: str = Field(min_length=1, max_length=255)
    new_name: str = Field(min_length=1, max_length=255)


class FileCopyRequest(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=50)
    destination: str = Field(min_length=1, max_length=4096)

    @field_validator("sources")
    @classmethod
    def validate_copy_sources(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            path = item.strip()
            if not path:
                raise ValueError("source paths cannot be empty")
            if len(path) > 4096:
                raise ValueError("source path is too long")
            cleaned.append(path)
        return cleaned


class FileMutationResult(BaseModel):
    success: bool = True
    message: str
    path: str | None = None
    paths: list[str] = Field(default_factory=list)


class FileDownloadTicketView(BaseModel):
    ticket: str
    expires_in: int
    path: str


class FileUrlDownloadRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    destination_path: str = Field(min_length=1, max_length=4096)
    filename: str | None = Field(default=None, max_length=255)
    overwrite: bool = False


class FileArchiveInspectRequest(BaseModel):
    archive_path: str = Field(min_length=1, max_length=4096)


class FileArchiveInspectView(BaseModel):
    archive_type: str
    folders: list[str] = Field(default_factory=list)
    entry_count: int = 0


class FileExtractRequest(BaseModel):
    archive_path: str = Field(min_length=1, max_length=4096)
    destination_path: str | None = Field(default=None, max_length=4096)
    overwrite: bool = False
    source_folder: str | None = Field(default=None, max_length=1024)
    strip_source_folder: bool = False


class FileTaskView(BaseModel):
    task_id: str
    status: str
    message: str | None = None
    error: str | None = None
    target_path: str | None = None
    destination: str | None = None
    elapsed_seconds: float | None = None


class ConsoleWorkspaceView(BaseModel):
    """Game and SSH console status. GET stays 200 when SSH is down."""

    server_id: int
    host: str
    session_manager: Literal["screen", "tmux"] = "tmux"
    ssh_ok: bool
    ssh_error: str | None = None
    game_running: bool = False
    steamcmd_running: bool = False
    message: str | None = None


class ConsolePaneView(BaseModel):
    """Live tmux/screen pane snapshot. GET stays 200 when SSH or the session is down."""

    server_id: int
    kind: Literal["game", "steamcmd"]
    session_name: str
    session_manager: Literal["screen", "tmux"] | None = None
    ssh_ok: bool
    running: bool = False
    text: str = ""
    heartbeat: str | None = None
    message: str | None = None


class AssistantConversationView(BaseModel):
    id: str
    server_id: int | None = None
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AssistantMessageView(BaseModel):
    id: int
    role: str
    content: str | None = None
    tool_name: str | None = None
    created_at: datetime | None = None


class AssistantConversationDetailView(AssistantConversationView):
    messages: list[AssistantMessageView] = Field(default_factory=list)


class AssistantWorkspaceView(BaseModel):
    provider_ready: bool
    mode: Literal["global", "custom", "none"]
    model: str | None = None
    conversations: list[AssistantConversationView] = Field(default_factory=list)


class AssistantConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    server_id: int | None = None


class AssistantMessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16000)


class AssistantRunView(BaseModel):
    id: str
    conversation_id: str
    status: str
    error: str | None = None


class AssistantToolView(BaseModel):
    id: str
    tool_name: str
    arguments_hash: str
    risk: str
    status: str
    requires_approval: bool
    error: str | None = None


class AssistantRunDetailView(AssistantRunView):
    tools: list[AssistantToolView] = Field(default_factory=list)


class AssistantToolDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    arguments_hash: str = Field(min_length=64, max_length=64)


class DiscordBotView(BaseModel):
    enabled: bool
    token_configured: bool
    message_trigger_mode: Literal["mention_only", "mention_and_greetings"]
    username: str | None = None
    connection_status: str
    last_error: str | None = None
    invite_url: str | None = None


class DiscordBotUpdateRequest(BaseModel):
    token: str | None = Field(default=None, min_length=20, max_length=4096)
    enabled: bool | None = None
    message_trigger_mode: Literal["mention_only", "mention_and_greetings"] | None = None


class DiscordBotTestBody(BaseModel):
    token: str | None = Field(default=None, min_length=20, max_length=4096)


class DiscordBotTestView(BaseModel):
    success: bool
    username: str | None = None
    message: str


class DiscordGuildView(BaseModel):
    id: str
    name: str
    icon: str | None = None


class DiscordChannelView(BaseModel):
    id: str
    guild_id: str
    name: str
    type: int = 0


class DiscordRoleView(BaseModel):
    id: str
    guild_id: str
    name: str
    position: int = 0


class DiscordOptionsView(BaseModel):
    """Guild/channel/role picker. GET stays 200 when no bot token is stored."""

    token_configured: bool
    guilds: list[DiscordGuildView] = Field(default_factory=list)
    channels: list[DiscordChannelView] = Field(default_factory=list)
    roles: list[DiscordRoleView] = Field(default_factory=list)
    message: str | None = None


class DiscordBindingView(BaseModel):
    server_id: int
    enabled: bool
    effective_enabled: bool
    disabled_reason: str | None = None
    guild_id: str | None = None
    channel_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    allow_channel_managers: bool = False
    allow_server_administrators: bool = False
    capabilities: list[str] = Field(default_factory=list)
    response_visibility: Literal["public"] = "public"


class DiscordBindingUpdateRequest(BaseModel):
    enabled: bool = False
    guild_id: str | None = None
    channel_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    allow_channel_managers: bool = False
    allow_server_administrators: bool = False
    capabilities: list[str] = Field(default_factory=list)
    sync_existing_servers: bool = False


class DiscordGlobalBindingView(BaseModel):
    configured: bool
    enabled: bool
    guild_id: str | None = None
    channel_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    allow_channel_managers: bool = False
    allow_server_administrators: bool = False
    capabilities: list[str] = Field(default_factory=list)
    server_count: int = 0
    matching_server_count: int = 0
    synced_server_count: int = 0
    inherited_by_new_servers: bool = True


class DiscordMenuPushBody(BaseModel):
    guild_id: str
    channel_id: str


class DiscordMenuPushView(BaseModel):
    guild_id: str
    channel_id: str
    message_id: str
    expires_in_seconds: int = 300


class AgentPolicyView(BaseModel):
    server_id: int
    enabled: bool
    effective_enabled: bool
    disabled_reason: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class AgentPolicyUpdateRequest(BaseModel):
    enabled: bool = True
    capabilities: list[str] = Field(default_factory=list)


class AssistantSystemSettingsView(BaseModel):
    """Admin AI provider. API keys stay write-only."""

    enabled: bool
    base_url: str | None = None
    model: str | None = None
    api_protocol: Literal["chat_completions", "responses"]
    api_key_configured: bool
    admin_prompt: str | None = None
    private_endpoint_allowlist: list[str] = Field(default_factory=list)
    reasoning_effort: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_completion_tokens: int = 2048
    token_limit_parameter: Literal["max_completion_tokens", "max_tokens", "omit"] = (
        "max_completion_tokens"
    )
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    verbosity: str | None = None
    parallel_tool_calls: bool | None = None
    request_timeout_seconds: int
    history_retention_days: int
    max_provider_rounds: int
    max_tool_calls_per_round: int
    provider_tested: bool
    tool_calling_tested: bool
    streaming_tested: bool


class AssistantSystemSettingsPatch(BaseModel):
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=255)
    api_protocol: Literal["chat_completions", "responses"] | None = None
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    admin_prompt: str | None = Field(default=None, max_length=8000)
    private_endpoint_allowlist: list[str] | None = None
    reasoning_effort: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_completion_tokens: int | None = Field(default=None, ge=256, le=32768)
    token_limit_parameter: Literal["max_completion_tokens", "max_tokens", "omit"] | None = None
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    verbosity: str | None = None
    parallel_tool_calls: bool | None = None
    request_timeout_seconds: int | None = Field(default=None, ge=5, le=120)
    history_retention_days: int | None = Field(default=None, ge=1, le=7)
    max_provider_rounds: int | None = Field(default=None, ge=1, le=1000)
    max_tool_calls_per_round: int | None = Field(default=None, ge=1, le=1000)


class AssistantProviderTestBody(BaseModel):
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=255)
    api_protocol: Literal["chat_completions", "responses"] | None = None
    api_key: str | None = Field(default=None, max_length=4096)


class AssistantProviderTestView(BaseModel):
    success: bool
    text_response_ok: bool
    tool_calling_ok: bool
    streaming_ok: bool
    message: str


class ScheduledTaskView(BaseModel):
    id: int
    server_id: int
    name: str
    action: str
    enabled: bool
    schedule_type: str
    schedule_value: str
    last_run: datetime | None = None
    next_run: datetime | None = None
    run_count: int = 0
    last_status: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ScheduledTaskCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    action: Literal["start", "stop", "restart", "update", "validate", "backup_plugins"]
    enabled: bool = True
    schedule_type: Literal["daily", "weekly", "interval", "cron"]
    schedule_value: str = Field(min_length=1, max_length=255)


class ScheduledTaskUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    action: Literal["start", "stop", "restart", "update", "validate", "backup_plugins"] | None = (
        None
    )
    enabled: bool | None = None
    schedule_type: Literal["daily", "weekly", "interval", "cron"] | None = None
    schedule_value: str | None = Field(default=None, min_length=1, max_length=255)


class PluginUpdatesView(BaseModel):
    enable_plugin_auto_update: bool
    plugin_update_check_interval_hours: float
    last_plugin_update_check: datetime | None = None
    enable_plugin_post_update_commands: bool = False
    plugin_post_update_command_ids: list[int] = Field(default_factory=list)
    plugins: list[ManagedPluginUpdateView] = Field(default_factory=list)


class PluginUpdatesSettingsRequest(BaseModel):
    enable_plugin_auto_update: bool
    plugin_update_check_interval_hours: float = Field(ge=0.0167, le=24.0)
    enable_plugin_post_update_commands: bool = False
    plugin_post_update_command_ids: list[int] = Field(default_factory=list)


class ManagedPluginRegisterRequest(BaseModel):
    """Register an already-installed plugin or framework for auto-update."""

    source_type: Literal["github", "market", "framework"] = "github"
    source_key: str | None = Field(default=None, max_length=500)
    display_name: str = Field(min_length=1, max_length=255)
    repo_url: str | None = Field(default=None, max_length=500)
    market_plugin_id: int | None = None
    framework_key: str | None = Field(default=None, max_length=100)
    installed_release_id: str | None = Field(default=None, max_length=100)
    installed_version: str = Field(default="unknown", max_length=100)
    asset_glob: str | None = Field(default=None, max_length=500)
    custom_install_path: str | None = Field(default=None, max_length=255)
    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_files: list[str] = Field(default_factory=list)
    auto_update_enabled: bool = False
    backup_before_update: bool = False
    restart_after_update: bool = False

    @field_validator("repo_url")
    @classmethod
    def validate_register_repo_url(cls, value: str | None) -> str | None:
        if not value:
            return value
        text = value.strip().rstrip("/")
        if not re.match(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$", text):
            raise ValueError("repo_url must be a GitHub repository URL")
        return text

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_register_exclusions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if ".." in text.split("/") or text.startswith("/") or "\x00" in text:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
            cleaned.append(text)
        return cleaned


class PluginUpdatesPluginPatch(BaseModel):
    auto_update_enabled: bool | None = None
    backup_before_update: bool | None = None
    restart_after_update: bool | None = None
    exclude_dirs: list[str] | None = None
    exclude_files: list[str] | None = None

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_update_exclusions(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        cleaned: list[str] = []
        for value in values:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if ".." in text.split("/") or text.startswith("/") or "\x00" in text:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
            cleaned.append(text)
        return cleaned


class PluginUpdateStatusView(BaseModel):
    state: str = "idle"
    phase: str = "idle"
    message: str | None = None
    current: int = 0
    total: int = 0
    logs: list[str] = Field(default_factory=list)
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None


class GameUpdatesView(BaseModel):
    """Steam advertised version versus installed steam.inf, plus game auto-update."""

    installed_version: str | None = None
    installed_build_id: str | None = None
    installed_source: Literal["steam.inf", "database", "unknown"] = "unknown"
    advertised_version: str | None = None
    up_to_date: bool | None = None
    steam_check_ok: bool = False
    steam_message: str | None = None
    steam_error: str | None = None
    enable_auto_update: bool = True
    update_check_interval_hours: float = 1.0
    last_update_check: datetime | None = None
    last_update_time: datetime | None = None
    current_game_version: str | None = None

    @field_validator(
        "installed_version",
        "installed_build_id",
        "advertised_version",
        "current_game_version",
        mode="before",
    )
    @classmethod
    def stringify_optional_version_fields(cls, value: object) -> str | None:
        """Redis JSON turns numeric build ids into ints; keep the public contract as text."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class GameUpdatesSettingsRequest(BaseModel):
    enable_auto_update: bool
    update_check_interval_hours: float = Field(ge=0.0167, le=24.0)


class GameUpdateOperationRequest(BaseModel):
    action: Literal["update", "validate"]


class CustomCommandView(BaseModel):
    """Saved host or game-process shortcut. Command text is user-authored, not a secret."""

    id: int
    server_id: int
    name: str
    target: Literal["host", "game_process"]
    commands: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CustomCommandWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    target: Literal["host", "game_process"] = "host"
    commands: str = Field(min_length=1, max_length=20000)


class CustomCommandExecuteBody(BaseModel):
    target: Literal["host", "game_process"] = "host"
    commands: str = Field(min_length=1, max_length=20000)


class CustomCommandExecuteView(BaseModel):
    success: bool
    message: str
    log: str = ""


class StartupCommandView(BaseModel):
    """Masked startup-command preview. Passwords and tokens are never returned in clear text."""

    startup_command: str
    cs2_command: str
    session_manager: str
    game_mode_resolved: str


class ConfirmDeploymentView(BaseModel):
    success: bool
    message: str
    status: str
    last_deployed: datetime | None = None


class CleanupItemView(BaseModel):
    path: str
    name: str
    type: str
    size: int = 0
    category: str
    reason: str
    danger_level: str


class CleanupWorkshopView(BaseModel):
    path: str
    item_count: int = 0
    size: int = 0


class CleanupScanView(BaseModel):
    safe_items: list[CleanupItemView] = Field(default_factory=list)
    archive_items: list[CleanupItemView] = Field(default_factory=list)
    workshop_summary: CleanupWorkshopView
    total_size: int = 0
    safe_item_count: int = 0
    archive_item_count: int = 0
    truncated: bool = False


class CleanupDeleteBody(BaseModel):
    mode: Literal["safe", "archives", "workshop"]
    paths: list[str] = Field(default_factory=list)
    confirmation_text: str | None = None


class CleanupFailedItemView(BaseModel):
    path: str
    error: str


class CleanupDeleteView(BaseModel):
    success: bool
    message: str
    deleted_count: int = 0
    freed_bytes_estimate: int = 0
    failed_items: list[CleanupFailedItemView] = Field(default_factory=list)


class CleanupSystemTargetView(BaseModel):
    id: str
    title: str
    reason: str
    size: int = 0
    needs_privilege: bool = False
    can_apply: bool = False
    command: str | None = None


class CleanupSystemScanView(BaseModel):
    privilege: Literal["root", "sudo", "none"]
    retain_days: int
    has_sudo_password: bool = False
    targets: list[CleanupSystemTargetView] = Field(default_factory=list)
    total_size: int = 0
    can_apply_privileged: bool = False
    manual_execute: list[str] = Field(default_factory=list)
    manual_setup: list[str] = Field(default_factory=list)


class CleanupSystemApplyBody(BaseModel):
    targets: list[str] = Field(min_length=1)
    retain_days: int | None = Field(default=None, ge=1, le=90)


class CleanupTargetResultView(BaseModel):
    id: str
    error: str


class CleanupSystemApplyView(BaseModel):
    success: bool
    message: str
    privilege: Literal["root", "sudo", "none"]
    applied: list[str] = Field(default_factory=list)
    skipped: list[CleanupTargetResultView] = Field(default_factory=list)
    failed: list[CleanupTargetResultView] = Field(default_factory=list)
    deleted_count: int = 0
    freed_bytes_estimate: int = 0
    manual_execute: list[str] = Field(default_factory=list)
    manual_setup: list[str] = Field(default_factory=list)


class CleanupPolicyView(BaseModel):
    enabled: bool = False
    retain_days: int = 7
    schedule_value: str = "03:30"
    targets: list[str] = Field(default_factory=list)
    has_sudo_password: bool = False
    last_run: datetime | None = None
    next_run: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    run_count: int = 0
    privilege: Literal["root", "sudo", "none"] | None = None
    manual_execute: list[str] = Field(default_factory=list)
    manual_setup: list[str] = Field(default_factory=list)
    message: str | None = None


class CleanupPolicyBody(BaseModel):
    enabled: bool
    retain_days: int = Field(default=7, ge=1, le=90)
    schedule_value: str = Field(default="03:30", min_length=4, max_length=5)
    targets: list[str] = Field(default_factory=list)


class InitializedHostView(BaseModel):
    """Saved auto-setup host. Credentials are never included on the list."""

    key: str
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    game_directory: str
    created_at: float


class InitializedHostCredentialsView(BaseModel):
    """Owner-only one-time reveal of a saved auto-setup host (Redis, 24h)."""

    key: str
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    ssh_password: str
    game_directory: str
    created_at: float


class AutoSetupRequest(BaseModel):
    """Create the dedicated CS2 Linux user and install host packages over SSH."""

    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=64)
    ssh_password: str = Field(min_length=1, max_length=255)
    sudo_password: str | None = None
    cs2_username: str = Field(default="cs2server", pattern=r"^[a-z_][a-z0-9_-]*$")
    cs2_password: str | None = None
    captcha_token: str = Field(min_length=1)
    captcha_code: str = Field(min_length=4, max_length=4)
    save_config: bool = True
    open_game_ports: bool = True
    session_id: str | None = None


class AutoSetupResultView(BaseModel):
    """Completed auto-setup. ``cs2_password`` is returned once so the operator can add the server."""

    success: bool
    message: str
    cs2_username: str
    cs2_password: str
    game_directory: str
    logs: list[str] = Field(default_factory=list)
    initialized_server_id: str | None = None


class ManualSetupScriptView(BaseModel):
    cs2_username: str
    password: str
    script: str


class PluginDiagnosticRecommendationView(BaseModel):
    recommended: bool
    reason: str | None = None
    recently_updated: bool = False
    last_update_time: datetime | None = None
    restart_count: int = 0
    max_restarts: int = 0
    window_minutes: int = 30


class PluginDiagnosticPlanBody(BaseModel):
    scope: Literal["metamod", "counterstrikesharp", "both"] = "both"


class PluginDiagnosticExecuteBody(PluginDiagnosticPlanBody):
    expected_plan_hash: str = Field(min_length=64, max_length=64)


class PluginDiagnosticPlanView(BaseModel):
    server_id: int
    scope: str
    plan_hash: str
    candidates: list[dict] = Field(default_factory=list)
    candidate_groups: list[dict] = Field(default_factory=list)
    estimated_max_starts: int = 0
    health_policy: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PluginDiagnosticRunView(BaseModel):
    id: str
    server_id: int
    requested_by: int
    scope: str
    status: str
    plan_hash: str
    culprit_keys: list[str] = Field(default_factory=list)
    start_attempts: int = 0
    error: str | None = None
    steps: list[dict] = Field(default_factory=list)
    quarantine: list[dict] = Field(default_factory=list)
    created_at: datetime | None = None
    completed_at: datetime | None = None


class GameModeMapView(BaseModel):
    name: str
    workshop_id: str


class GameModeSummaryView(BaseModel):
    id: str
    launch_upsert: dict[str, str]
    frameworks: list[str]
    market_plugin_titles: list[str]
    maps: list[GameModeMapView]
    plugin_config: dict[str, object]
    startup_workshop_map: str
    present: dict[str, bool | None]
    missing_market_plugins: list[str] = Field(default_factory=list)


class GameModeCatalogView(BaseModel):
    server_id: int
    reachable: bool
    additional_parameters: str | None = None
    addons_path: str
    addons_present: bool | None = None
    swiftly_installed: bool | None = None
    modes: list[GameModeSummaryView] = Field(default_factory=list)


class GameModeMutationView(BaseModel):
    id: str
    target: str
    before: object | None = None
    after: object | None = None
    destructive: bool = False
    status: str


class GameModeStepView(BaseModel):
    id: str
    action: str
    status: str
    destructive: bool = False
    path: str | None = None
    title: str | None = None
    plugin_id: int | None = None
    framework: str | None = None
    name: str | None = None
    workshop_id: str | None = None
    values: dict[str, object] | None = None
    files: list[str] | None = None


class GameModeStartupView(BaseModel):
    before: str | None = None
    after: str | None = None
    changed: bool = False


class GameModePreflightRequest(BaseModel):
    wipe_addons: bool = False


class GameModeInstallRequest(BaseModel):
    wipe_addons: bool = False
    wipe_addons_acknowledged: bool = False
    plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)


class GameModePlanView(BaseModel):
    server_id: int
    mode_id: str
    wipe_addons: bool
    addons_path: str
    current: dict[str, bool]
    startup: GameModeStartupView
    plugin_config: dict[str, object]
    maps: list[GameModeMapView]
    wait_files: list[str]
    plugin_plans: dict[str, PluginInstallPlanView] = Field(default_factory=dict)
    hard_conflicts: list[PluginConflictView] = Field(default_factory=list)
    warnings: list[PluginConflictView] = Field(default_factory=list)
    steps: list[GameModeStepView] = Field(default_factory=list)
    mutations: list[GameModeMutationView] = Field(default_factory=list)
    blocked: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    plan_hash: str
