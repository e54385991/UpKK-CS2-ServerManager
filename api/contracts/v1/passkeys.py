"""Passkey (WebAuthn) HTTP DTOs for the versioned console contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from api.contracts.base import ApiRequest, ApiResponse


class PasskeyView(ApiResponse):
    """Public projection of a stored passkey. The public key never leaves the database."""

    id: int
    nickname: str = ""
    transports: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    backup_eligible: bool | None = None
    backup_state: bool | None = None


class PasskeyListView(ApiResponse):
    items: list[PasskeyView]


class PasskeyOptionsView(ApiResponse):
    """PublicKeyCredentialCreationOptions or RequestOptions as JSON (base64url bytes)."""

    public_key: dict[str, Any]


class PasskeyLoginOptionsRequest(ApiRequest):
    username: str | None = Field(default=None, max_length=100)


class PasskeyCredentialRequest(ApiRequest):
    """Browser WebAuthn credential JSON plus an optional display name on register."""

    credential: dict[str, Any]
    nickname: str | None = Field(default=None, max_length=100)


class PasskeyPatchRequest(ApiRequest):
    nickname: str = Field(min_length=1, max_length=100)
