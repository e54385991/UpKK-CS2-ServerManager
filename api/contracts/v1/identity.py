"""Identity, profile and per-user AI transport contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import EmailStr, Field, field_validator

from api.contracts.base import ApiRequest, ApiResponse

ItemT = TypeVar("ItemT")


class V1Model(ApiResponse):
    """Compatibility base for response models in the versioned contract."""


class Page(V1Model, Generic[ItemT]):
    """Unified offset-based pagination container for list endpoints."""

    items: list[ItemT]
    total: int
    limit: int
    offset: int


class ProblemDetail(V1Model):
    """RFC 9457-style error body used by the versioned API."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None


class SessionUser(V1Model):
    """The authenticated principal as the console needs it."""

    id: int
    username: str
    email: str | None = None
    is_admin: bool
    is_active: bool


class RegisterRequest(ApiRequest):
    """Public self-registration body; creates a non-admin member."""

    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=100)
    captcha_token: str | None = Field(default=None, min_length=1)
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)


class PasswordResetEmailRequest(ApiRequest):
    """Public forgot-password body; does not reveal whether the email exists."""

    email: EmailStr
    captcha_token: str | None = Field(default=None, min_length=1)
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)


class PasswordResetCompleteRequest(ApiRequest):
    """Public reset-password body. The token is the one-time value from the email link."""

    token: str = Field(min_length=1)
    new_password: str = Field(min_length=6, max_length=100)


class GoogleConfigView(V1Model):
    """Public Google OAuth client configuration. ``client_id`` is the browser OAuth client."""

    client_id: str = ""
    enabled: bool = False


class GoogleSignInRequest(ApiRequest):
    """Google identity-token sign-in. New accounts also send a username and password."""

    id_token: str = Field(min_length=1)
    username: str | None = Field(default=None, min_length=3, max_length=100)
    password: str | None = Field(default=None, min_length=6, max_length=100)


class AuthTokenView(V1Model):
    """Session token body. The HttpOnly cookie is the browser session; this mirrors login."""

    access_token: str
    token_type: str = "bearer"


class ProfileView(V1Model):
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


class ProfilePatch(ApiRequest):
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


class ProfilePasswordChange(ApiRequest):
    """Change the signed-in user's password. The CAPTCHA is policy-controlled."""

    current_password: str = Field(min_length=6, max_length=100)
    new_password: str = Field(min_length=6, max_length=100)
    confirm_password: str = Field(min_length=6, max_length=100)
    captcha_token: str | None = Field(default=None, min_length=1)
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)


class ProfileApiKeyView(V1Model):
    """The user's own panel API key. Returned only on this dedicated reveal endpoint."""

    api_key: str
    created_at: datetime | None = None


class ProfileApiKeyGenerate(ApiRequest):
    """Optional captcha when rotating the personal API key from the console."""

    captcha_token: str | None = None
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)


class ProfileGsltGenerate(ApiRequest):
    """Create a Steam GSLT with the signed-in user's Steam Web API key."""

    server_name: str | None = Field(default=None, max_length=255)
    captcha_token: str | None = Field(default=None, min_length=1)
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)


class ProfileGsltView(V1Model):
    """Newly generated GSLT. Returned once so the operator can save it on a server."""

    login_token: str
    steamid: str | None = None


class ProfileS3View(V1Model):
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


class ProfileS3Patch(ApiRequest):
    """Partial S3 backup update. Secret is write-only; CAPTCHA is policy-controlled."""

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
    captcha_token: str | None = Field(default=None, min_length=1)
    captcha_code: str | None = Field(default=None, min_length=4, max_length=4)

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


class ProfileS3TestStep(V1Model):
    name: str
    status: str
    message: str


class ProfileS3TestView(V1Model):
    success: bool
    message: str
    steps: list[ProfileS3TestStep] = Field(default_factory=list)


class AssistantUserSettingsView(V1Model):
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


class AssistantUserSettingsPatch(ApiRequest):
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


__all__ = [
    "V1Model",
    "V1Model",
    "Page",
    "ProblemDetail",
    "SessionUser",
    "RegisterRequest",
    "PasswordResetEmailRequest",
    "PasswordResetCompleteRequest",
    "GoogleConfigView",
    "GoogleSignInRequest",
    "AuthTokenView",
    "ProfileView",
    "ProfilePatch",
    "ProfilePasswordChange",
    "ProfileApiKeyView",
    "ProfileApiKeyGenerate",
    "ProfileGsltGenerate",
    "ProfileGsltView",
    "ProfileS3View",
    "ProfileS3Patch",
    "ProfileS3TestStep",
    "ProfileS3TestView",
    "AssistantUserSettingsView",
    "AssistantUserSettingsPatch",
]
