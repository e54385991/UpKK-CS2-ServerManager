"""System schemas."""

# ruff: noqa: F403,F405

from .common import *


class SystemSettingsResponse(SQLModel):
    """Schema for system settings response"""

    id: int
    default_proxy_mode: str
    github_proxy_url: Optional[str]
    has_global_github_token: bool
    global_github_token_prefix: Optional[str]
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
    global_github_token: Optional[str] = Field(default=None, max_length=255)
    clear_global_github_token: bool = False
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

    @field_validator("default_proxy_mode")
    @classmethod
    def validate_proxy_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("direct", "panel", "github_url"):
            raise ValueError("default_proxy_mode must be one of: direct, panel, github_url")
        return v

    @field_validator("global_github_token")
    @classmethod
    def validate_global_github_token(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return v
        token = v.strip()
        if not re.match(r"^(github_pat_[A-Za-z0-9_]+|gh[poushr]_[A-Za-z0-9_]+)$", token):
            raise ValueError("Global GitHub token must be a valid Fine-grained or Classic token")
        return token

    @field_validator("email_provider")
    @classmethod
    def validate_email_provider(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("gmail", "smtp"):
            raise ValueError("email_provider must be one of: gmail, smtp")
        return v


class ForgotPasswordRequest(SQLModel):
    """Schema for forgot password request"""

    email: EmailStr
    captcha_token: str = Field(..., description="CAPTCHA token from /api/captcha/generate")
    captcha_code: str = Field(
        ..., min_length=4, max_length=4, description="User-entered CAPTCHA code"
    )


class ResetPasswordRequest(SQLModel):
    """Schema for reset password request"""

    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=100)


class GmailCredentialsUploadRequest(SQLModel):
    """Schema for Gmail OAuth credentials upload"""

    credentials_json: str = Field(
        ...,
        min_length=1,
        description="The contents of the credentials.json file from Google Cloud Console",
    )


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
    discord_notify_crash_restarts: bool
    discord_crash_restart_min_interval_minutes: int


class DiscordSettingsUpdate(SQLModel):
    """Schema for updating per-server Discord notification settings"""

    discord_notifications_enabled: Optional[bool] = None
    discord_webhook_url: Optional[str] = Field(default=None, max_length=1000)
    discord_channel_name: Optional[str] = Field(default=None, max_length=255)
    discord_notify_auto_updates: Optional[bool] = None
    discord_notify_manual_updates: Optional[bool] = None
    discord_notify_plugin_updates: Optional[bool] = None
    discord_notify_s3_backups: Optional[bool] = None
    discord_notify_crash_restarts: Optional[bool] = None
    discord_crash_restart_min_interval_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    clear_webhook: bool = False

    @field_validator("discord_channel_name")
    @classmethod
    def normalize_channel_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        return v or None

    @field_validator("discord_webhook_url")
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
    username: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=100,
        description="Username for new account (if registering)",
    )
    password: Optional[str] = Field(
        default=None,
        min_length=6,
        max_length=100,
        description="Password for new account (if registering)",
    )
