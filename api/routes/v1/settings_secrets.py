"""Explicitly requested settings secrets import and export validation."""

from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import HTTPException, status

from modules import AISystemSettings, SystemSettings
from services.ai_security import AIConfigurationError

from .schemas import SystemSettingsImportRequest, SystemSettingsSecretTransfer


def _secret_transfer(
    settings: SystemSettings,
    ai_settings: AISystemSettings,
    decrypt: Callable[[str | None], str | None],
) -> SystemSettingsSecretTransfer:
    try:
        ai_api_key = decrypt(ai_settings.api_key_encrypted)
    except AIConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The saved AI API key cannot be decrypted; re-enter it before exporting secrets",
        ) from exc
    return SystemSettingsSecretTransfer(
        global_github_token=settings.global_github_token,
        smtp_password=settings.smtp_password,
        gmail_credentials_json=settings.gmail_credentials_json,
        gmail_token_json=settings.gmail_token_json,
        ai_api_key=ai_api_key,
    )


def _validate_json_secret(value: str, *, credentials: bool) -> None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        label = "Gmail credentials" if credentials else "Gmail token"
        raise HTTPException(status_code=422, detail=f"{label} must be valid JSON") from exc
    if not isinstance(parsed, dict) or (credentials and not {"web", "installed"} & parsed.keys()):
        label = "credentials" if credentials else "token"
        raise HTTPException(status_code=422, detail=f"Invalid Gmail {label} JSON structure")


def _import_system_secrets(
    settings: SystemSettings, body: SystemSettingsImportRequest
) -> tuple[list[str], list[str]]:
    imported_secret_fields: list[str] = []
    preserved_secret_fields: list[str] = []
    secrets = body.secrets
    if secrets is None:
        preserved_secret_fields = [
            "global_github_token",
            "smtp_password",
            "gmail_credentials_json",
            "gmail_token_json",
            "ai_api_key",
        ]
    else:
        if secrets.global_github_token:
            settings.global_github_token = secrets.global_github_token
            settings.github_token_fingerprint = None
            settings.github_token_verification = None
            imported_secret_fields.append("global_github_token")
        else:
            preserved_secret_fields.append("global_github_token")
        if secrets.smtp_password:
            settings.smtp_password = secrets.smtp_password
            imported_secret_fields.append("smtp_password")
        else:
            preserved_secret_fields.append("smtp_password")
        if secrets.gmail_credentials_json:
            _validate_json_secret(secrets.gmail_credentials_json, credentials=True)
            settings.gmail_credentials_json = secrets.gmail_credentials_json
            imported_secret_fields.append("gmail_credentials_json")
        else:
            preserved_secret_fields.append("gmail_credentials_json")
        if secrets.gmail_token_json:
            _validate_json_secret(secrets.gmail_token_json, credentials=False)
            settings.gmail_token_json = secrets.gmail_token_json
            imported_secret_fields.append("gmail_token_json")
        else:
            preserved_secret_fields.append("gmail_token_json")
        if not secrets.ai_api_key:
            preserved_secret_fields.append("ai_api_key")

    return imported_secret_fields, preserved_secret_fields
