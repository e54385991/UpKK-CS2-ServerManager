"""System and profile-adjacent settings contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field, field_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model
from modules.utils import normalize_client_ip_header, normalize_log_level

ProxyMode = Literal["direct", "panel", "github_url"]
EmailProvider = Literal["gmail", "smtp"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class GitHubTokenVerificationView(V1Model):
    valid: bool
    account: str | None = None
    checked_at: str | None = None
    core_remaining: int | None = None
    core_reset: int | None = None
    search_remaining: int | None = None
    search_reset: int | None = None
    message: str


class SystemSettingsView(V1Model):
    """Admin system settings with secrets replaced by presence flags."""

    default_proxy_mode: ProxyMode
    github_proxy_url: str | None = None
    captcha_enabled: bool = True
    client_ip_header: str | None = None
    # None means the console follows the LOG_LEVEL environment variable.
    log_level: LogLevel | None = None
    effective_log_level: LogLevel
    github_token_verification: GitHubTokenVerificationView | None = None
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


class SystemSettingsPatch(ApiRequest):
    """Partial admin update. Secret fields are write-only and never echoed."""

    default_proxy_mode: ProxyMode | None = None
    github_proxy_url: str | None = None
    captcha_enabled: bool | None = None
    client_ip_header: str | None = Field(default=None, max_length=64)
    log_level: str | None = Field(default=None, max_length=16)
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

    @field_validator("client_ip_header")
    @classmethod
    def validate_client_ip_header(cls, value: str | None) -> str | None:
        """Blank clears the policy, so the panel trusts only the socket peer."""
        return normalize_client_ip_header(value)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str | None) -> str | None:
        """Blank clears the override, so the console follows the environment."""
        return normalize_log_level(value)

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


class EmailTestRequest(ApiRequest):
    """Send a test message through the currently saved email configuration."""

    test_email: EmailStr


class EmailTestResult(V1Model):
    success: bool
    message: str


class GmailCredentialsUpload(ApiRequest):
    """Write-only Google Cloud OAuth client JSON for Gmail API."""

    credentials_json: str = Field(min_length=1)


class GmailAuthorizeResult(V1Model):
    authorization_url: str
    state: str | None = None


class ActionResult(V1Model):
    success: bool
    message: str


__all__ = [
    "SystemSettingsView",
    "SystemSettingsPatch",
    "EmailTestRequest",
    "EmailTestResult",
    "GmailCredentialsUpload",
    "GmailAuthorizeResult",
    "ActionResult",
    "ProxyMode",
    "EmailProvider",
    "LogLevel",
]
