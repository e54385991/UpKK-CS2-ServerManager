"""System and profile-adjacent settings contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, cast

from pydantic import EmailStr, Field, field_validator, model_validator

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model
from modules.utils import normalize_client_ip_header, normalize_log_level

ProxyMode = Literal["direct", "panel", "github_url"]
EmailProvider = Literal["gmail", "smtp"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AIAPIProtocol = Literal["chat_completions", "responses"]
AITokenLimitParameter = Literal["max_completion_tokens", "max_tokens", "omit"]
AIContextWindowTokens = Literal[
    8192,
    16384,
    32768,
    65536,
    131072,
    262144,
    393216,
    1048576,
]


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
    plugin_download_cache_enabled: bool = True
    plugin_download_cache_path: str | None = None
    plugin_download_cache_files: int = 0
    plugin_download_cache_bytes: int = 0
    # 0 means the matching automatic-cleanup limit is switched off.
    plugin_download_cache_max_age_days: int = 30
    plugin_download_cache_max_megabytes: int = 4096
    captcha_enabled: bool = True
    registration_enabled: bool = True
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
    plugin_download_cache_enabled: bool | None = None
    plugin_download_cache_path: str | None = Field(default=None, max_length=1000)
    plugin_download_cache_max_age_days: int | None = Field(default=None, ge=0, le=3650)
    plugin_download_cache_max_megabytes: int | None = Field(default=None, ge=0, le=1_048_576)
    github_proxy_url: str | None = None
    captcha_enabled: bool | None = None
    registration_enabled: bool | None = None
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


class SystemSettingsTransfer(V1Model):
    """Portable non-secret global settings; Discord data is intentionally absent."""

    default_proxy_mode: ProxyMode = "panel"
    github_proxy_url: str | None = Field(default=None, max_length=500)
    plugin_download_cache_enabled: bool = True
    plugin_download_cache_path: str | None = Field(default=None, max_length=1000)
    plugin_download_cache_max_age_days: int = Field(default=30, ge=0, le=3650)
    plugin_download_cache_max_megabytes: int = Field(default=4096, ge=0, le=1_048_576)
    captcha_enabled: bool = True
    registration_enabled: bool = True
    client_ip_header: str | None = Field(default=None, max_length=64)
    log_level: LogLevel | None = None
    email_enabled: bool = False
    email_provider: EmailProvider = "gmail"
    email_from_address: str | None = Field(default=None, max_length=255)
    email_from_name: str | None = Field(default=None, max_length=255)
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=255)
    smtp_use_tls: bool = True

    @field_validator("client_ip_header")
    @classmethod
    def validate_client_ip_header(cls, value: str | None) -> str | None:
        return normalize_client_ip_header(value)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: LogLevel | None) -> LogLevel | None:
        normalized = normalize_log_level(value)
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return None
        return cast(LogLevel, normalized)

    @field_validator(
        "github_proxy_url",
        "plugin_download_cache_path",
        "email_from_address",
        "email_from_name",
        "smtp_host",
        "smtp_username",
    )
    @classmethod
    def empty_string_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AISystemSettingsTransfer(V1Model):
    """Portable non-secret global AI settings; the API key is separate."""

    enabled: bool = False
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=255)
    api_protocol: AIAPIProtocol = "chat_completions"
    admin_prompt: str | None = Field(default=None, max_length=8000)
    private_endpoint_allowlist: list[str] = Field(default_factory=list)
    reasoning_effort: str | None = Field(default=None, max_length=16)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_completion_tokens: int = Field(default=2048, ge=256, le=32768)
    token_limit_parameter: AITokenLimitParameter = "max_completion_tokens"
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    verbosity: str | None = Field(default=None, max_length=16)
    parallel_tool_calls: bool | None = None
    context_window_tokens: AIContextWindowTokens = 262144
    requests_per_minute: int = Field(default=60, ge=1, le=10000)
    request_timeout_seconds: int = Field(default=60, ge=5, le=120)
    history_retention_days: int = Field(default=7, ge=1, le=7)
    max_provider_rounds: int = Field(default=200, ge=1, le=1000)
    max_tool_calls_per_round: int = Field(default=200, ge=1, le=1000)

    @field_validator("base_url", "model", "admin_prompt", "reasoning_effort", "verbosity")
    @classmethod
    def empty_string_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def use_one_sampling_control(self) -> "AISystemSettingsTransfer":
        if self.temperature is not None and self.top_p is not None:
            raise ValueError("Set temperature or top_p, not both")
        return self


class SystemSettingsSecretTransfer(V1Model):
    """Optional credentials included only after an explicit export choice."""

    global_github_token: str | None = Field(default=None, max_length=255)
    smtp_password: str | None = Field(default=None, max_length=255)
    gmail_credentials_json: str | None = Field(default=None, max_length=100_000)
    gmail_token_json: str | None = Field(default=None, max_length=100_000)
    ai_api_key: str | None = Field(default=None, max_length=4096)

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
        "global_github_token",
        "smtp_password",
        "gmail_credentials_json",
        "gmail_token_json",
        "ai_api_key",
    )
    @classmethod
    def blank_secret_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SystemSettingsExport(V1Model):
    """Portable system-settings bundle. It never contains Discord records."""

    format: Literal["upkk-system-settings"] = "upkk-system-settings"
    version: Literal[1] = 1
    exported_at: datetime | None = None
    include_secrets: bool = False
    system: SystemSettingsTransfer
    ai: AISystemSettingsTransfer
    secrets: SystemSettingsSecretTransfer | None = None


class SystemSettingsImportRequest(ApiRequest):
    """Import a system-settings bundle without touching servers or Discord."""

    format: Literal["upkk-system-settings"]
    version: Literal[1]
    exported_at: datetime | None = None
    include_secrets: bool = False
    system: SystemSettingsTransfer
    ai: AISystemSettingsTransfer
    secrets: SystemSettingsSecretTransfer | None = None

    @model_validator(mode="after")
    def require_explicit_secret_flag(self) -> "SystemSettingsImportRequest":
        if self.secrets is not None and not self.include_secrets:
            raise ValueError("Secret fields require include_secrets=true")
        return self


class SystemSettingsImportResult(V1Model):
    success: bool
    message: str
    ai_enabled_without_key: bool = False
    updated_fields: list[str] = Field(default_factory=list)
    imported_secret_fields: list[str] = Field(default_factory=list)
    preserved_secret_fields: list[str] = Field(default_factory=list)


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
    "SystemSettingsTransfer",
    "AISystemSettingsTransfer",
    "SystemSettingsSecretTransfer",
    "SystemSettingsExport",
    "SystemSettingsImportRequest",
    "SystemSettingsImportResult",
    "EmailTestRequest",
    "EmailTestResult",
    "GmailCredentialsUpload",
    "GmailAuthorizeResult",
    "ActionResult",
    "ProxyMode",
    "EmailProvider",
    "LogLevel",
]
