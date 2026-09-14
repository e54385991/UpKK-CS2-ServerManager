"""Versioned admin system-settings endpoints (non-secret projections)."""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request, status

from api.contracts.v1.settings import GitHubTokenVerificationView
from api.dependencies import AdminUser, DatabaseSession
from api.routes import ai as legacy_ai
from api.routes.ai_helpers import (
    _apply_system_enabled,
    _apply_system_provider_fields,
    _apply_system_runtime_limits,
)
from api.routes.gmail_oauth import (
    gmail_oauth_authorize,
    revoke_gmail_authorization,
    upload_gmail_credentials,
)
from modules import AISystemSettings, GmailCredentialsUploadRequest, SystemSettings
from modules.schemas.ai import AIProviderTestRequest, AISystemSettingsUpdate
from services.ai_security import AIConfigurationError, decrypt_credential
from services.audit_log_service import record_audit_event
from services.client_ip import set_client_ip_header
from services.email_service import email_service
from services.google_oauth import (
    effective_google_client_id,
    stored_google_client_id,
)
from services.log_output import apply_console_log_level, effective_console_log_level
from services.plugin_download_cache import (
    CachePolicy,
)
from services.plugin_download_cache import (
    clear as clear_download_cache,
)
from services.plugin_download_cache import (
    prune as prune_download_cache,
)
from services.plugin_download_cache import (
    stats as download_cache_stats,
)
from services.plugins.ai_import_store import verify_token

from .schemas import (
    ActionResult,
    AISystemSettingsTransfer,
    AssistantProviderTestBody,
    AssistantProviderTestView,
    AssistantSystemSettingsPatch,
    AssistantSystemSettingsView,
    EmailTestRequest,
    EmailTestResult,
    GmailAuthorizeResult,
    GmailCredentialsUpload,
    LogLevel,
    ProxyMode,
    SystemSettingsExport,
    SystemSettingsImportRequest,
    SystemSettingsImportResult,
    SystemSettingsPatch,
    SystemSettingsSecretTransfer,
    SystemSettingsTransfer,
    SystemSettingsView,
)

router = APIRouter(prefix="/api/v1/settings", tags=["v1-settings"])
logger = logging.getLogger(__name__)


ContextWindowToken = Literal[
    8192,
    16384,
    32768,
    65536,
    131072,
    262144,
    393216,
    1048576,
]


def _context_window_tokens(
    value: object,
) -> ContextWindowToken:
    supported: dict[int, ContextWindowToken] = {
        8192: 8192,
        16384: 16384,
        32768: 32768,
        65536: 65536,
        131072: 131072,
        262144: 262144,
        393216: 393216,
        1048576: 1048576,
    }
    if isinstance(value, int) and not isinstance(value, bool):
        return supported.get(value, 262144)
    return 262144


def _log_level(value: object) -> LogLevel | None:
    """Narrow a stored level name to the contract's literal set."""
    supported: dict[str, LogLevel] = {
        "DEBUG": "DEBUG",
        "INFO": "INFO",
        "WARNING": "WARNING",
        "ERROR": "ERROR",
        "CRITICAL": "CRITICAL",
    }
    if isinstance(value, str):
        return supported.get(value.strip().upper())
    return None


def _proxy_mode(value: object) -> ProxyMode:
    if value == "direct":
        return "direct"
    if value == "github_url":
        return "github_url"
    return "panel"


def _ai_protocol(value: object) -> Literal["chat_completions", "responses"]:
    return "responses" if value == "responses" else "chat_completions"


def _token_limit_parameter(value: object) -> Literal["max_completion_tokens", "max_tokens", "omit"]:
    if value in {"max_completion_tokens", "max_tokens", "omit"}:
        return cast(Literal["max_completion_tokens", "max_tokens", "omit"], value)
    return "max_completion_tokens"


def _github_verification_view(settings: SystemSettings) -> GitHubTokenVerificationView:
    from services.plugins.ai_import_store import verification_for

    return GitHubTokenVerificationView(**verification_for(settings).model_dump())


async def _apply_audit_retention_now() -> None:
    from services.audit_retention_service import audit_retention_service

    try:
        await audit_retention_service.cleanup_once()
    except Exception:
        logger.exception("Immediate audit retention cleanup failed")


def to_view(settings: SystemSettings) -> SystemSettingsView:
    """Project the ORM row to the browser-facing, non-secret view."""
    has_gmail_credentials = bool((settings.gmail_credentials_json or "").strip())
    has_gmail_token = bool((settings.gmail_token_json or "").strip())
    provider: Literal["gmail", "smtp"] = "gmail" if settings.email_provider == "gmail" else "smtp"
    download_cache = download_cache_stats(settings.plugin_download_cache_path)
    if settings.default_proxy_mode == "direct":
        proxy_mode: Literal["direct", "panel", "github_url"] = "direct"
    elif settings.default_proxy_mode == "github_url":
        proxy_mode = "github_url"
    else:
        proxy_mode = "panel"
    stored_google = stored_google_client_id(settings)
    effective_google = effective_google_client_id(settings)
    return SystemSettingsView(
        default_proxy_mode=proxy_mode,
        github_proxy_url=settings.github_proxy_url,
        plugin_download_cache_enabled=settings.plugin_download_cache_enabled,
        plugin_download_cache_path=settings.plugin_download_cache_path,
        plugin_download_cache_files=download_cache["files"],
        plugin_download_cache_bytes=download_cache["bytes"],
        plugin_download_cache_max_age_days=settings.plugin_download_cache_max_age_days,
        plugin_download_cache_max_megabytes=settings.plugin_download_cache_max_megabytes,
        captcha_enabled=bool(settings.captcha_enabled),
        registration_enabled=bool(settings.registration_enabled),
        google_client_id=stored_google or None,
        effective_google_client_id=effective_google,
        google_login_enabled=bool(effective_google),
        google_login_from_environment=not stored_google and bool(effective_google),
        client_ip_header=settings.client_ip_header,
        log_level=_log_level(settings.log_level),
        effective_log_level=_log_level(effective_console_log_level(settings.log_level)) or "INFO",
        audit_log_retention_days=int(getattr(settings, "audit_log_retention_days", 30) or 30),
        panel_monitoring_enabled=bool(getattr(settings, "panel_monitoring_enabled", True)),
        github_token_verification=_github_verification_view(settings),
        has_global_github_token=settings.has_global_github_token,
        global_github_token_prefix=settings.global_github_token_prefix,
        email_enabled=settings.email_enabled,
        email_provider=provider,
        email_from_address=settings.email_from_address,
        email_from_name=settings.email_from_name,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_use_tls=settings.smtp_use_tls,
        has_smtp_password=bool((settings.smtp_password or "").strip()),
        has_gmail_credentials=has_gmail_credentials,
        has_gmail_token=has_gmail_token,
        gmail_ready=has_gmail_credentials and has_gmail_token,
        updated_at=settings.updated_at,
    )


def _system_transfer(settings: SystemSettings) -> SystemSettingsTransfer:
    return SystemSettingsTransfer(
        default_proxy_mode=_proxy_mode(settings.default_proxy_mode),
        github_proxy_url=settings.github_proxy_url,
        plugin_download_cache_enabled=settings.plugin_download_cache_enabled,
        plugin_download_cache_path=settings.plugin_download_cache_path,
        plugin_download_cache_max_age_days=settings.plugin_download_cache_max_age_days,
        plugin_download_cache_max_megabytes=settings.plugin_download_cache_max_megabytes,
        captcha_enabled=settings.captcha_enabled,
        registration_enabled=settings.registration_enabled,
        google_client_id=stored_google_client_id(settings) or None,
        client_ip_header=settings.client_ip_header,
        log_level=_log_level(settings.log_level),
        audit_log_retention_days=int(getattr(settings, "audit_log_retention_days", 30) or 30),
        panel_monitoring_enabled=bool(getattr(settings, "panel_monitoring_enabled", True)),
        email_enabled=settings.email_enabled,
        email_provider="gmail" if settings.email_provider == "gmail" else "smtp",
        email_from_address=settings.email_from_address,
        email_from_name=settings.email_from_name,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_use_tls=settings.smtp_use_tls,
    )


def _ai_transfer(settings: AISystemSettings) -> AISystemSettingsTransfer:
    return AISystemSettingsTransfer(
        enabled=settings.enabled,
        base_url=settings.base_url,
        model=settings.model,
        api_protocol=_ai_protocol(settings.api_protocol),
        admin_prompt=settings.admin_prompt,
        private_endpoint_allowlist=list(settings.private_endpoint_allowlist or []),
        reasoning_effort=settings.reasoning_effort,
        temperature=settings.temperature,
        top_p=settings.top_p,
        max_completion_tokens=settings.max_completion_tokens,
        token_limit_parameter=_token_limit_parameter(settings.token_limit_parameter),
        frequency_penalty=settings.frequency_penalty,
        presence_penalty=settings.presence_penalty,
        verbosity=settings.verbosity,
        parallel_tool_calls=settings.parallel_tool_calls,
        context_window_tokens=_context_window_tokens(settings.context_window_tokens),
        requests_per_minute=settings.requests_per_minute,
        request_timeout_seconds=settings.request_timeout_seconds,
        history_retention_days=settings.history_retention_days,
        max_provider_rounds=settings.max_provider_rounds,
        max_tool_calls_per_round=settings.max_tool_calls_per_round,
    )


def _secret_transfer(
    settings: SystemSettings,
    ai_settings: AISystemSettings,
) -> SystemSettingsSecretTransfer:
    try:
        ai_api_key = decrypt_credential(ai_settings.api_key_encrypted)
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


def _ai_import_request(
    body: SystemSettingsImportRequest,
) -> AISystemSettingsUpdate:
    values = body.ai.model_dump()
    values["api_key"] = body.secrets.ai_api_key if body.secrets else None
    return AISystemSettingsUpdate(**values)


@router.get("/export", response_model=SystemSettingsExport)
async def export_system_settings(
    db: DatabaseSession,
    current_user: AdminUser,
    include_secrets: bool = Query(
        default=False,
        description="Include global credentials explicitly requested by the administrator.",
    ),
) -> SystemSettingsExport:
    """Return global system and AI settings without any Discord data."""
    settings = await SystemSettings.get_or_create_settings(db)
    ai_settings = await AISystemSettings.get_or_create(db)
    return SystemSettingsExport(
        exported_at=datetime.now(timezone.utc),
        include_secrets=include_secrets,
        system=_system_transfer(settings),
        ai=_ai_transfer(ai_settings),
        secrets=_secret_transfer(settings, ai_settings) if include_secrets else None,
    )


@router.post("/import", response_model=SystemSettingsImportResult)
async def import_system_settings(
    body: SystemSettingsImportRequest,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> SystemSettingsImportResult:
    """Import global system and AI settings; server, user, and Discord data stay untouched."""
    settings = await SystemSettings.get_or_create_settings(db)
    ai_settings = await AISystemSettings.get_or_create(db)
    system_data = body.system.model_dump()
    if system_data.get("panel_monitoring_enabled") is None:
        system_data.pop("panel_monitoring_enabled", None)
    old_cache_policy = (
        settings.plugin_download_cache_path,
        settings.plugin_download_cache_max_age_days,
        settings.plugin_download_cache_max_megabytes,
    )
    imported_secret_fields: list[str] = []
    preserved_secret_fields: list[str] = []
    ai_enabled_without_key = False
    try:
        settings.sqlmodel_update(system_data)
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

        ai_request = _ai_import_request(body)
        ai_enabled_without_key = bool(
            ai_request.enabled
            and not ai_settings.api_key_encrypted
            and not (secrets and secrets.ai_api_key)
        )
        if ai_enabled_without_key:
            # A redacted export must remain importable on a fresh instance;
            # keep the provider configuration but do not create an unusable enabled state.
            ai_request.enabled = False
        changed_provider = _apply_system_provider_fields(ai_settings, ai_request)
        _apply_system_runtime_limits(ai_settings, ai_request)
        if changed_provider:
            ai_settings.provider_tested = False
            ai_settings.tool_calling_tested = False
            ai_settings.streaming_tested = False
        _apply_system_enabled(ai_settings, ai_request)
        if secrets and secrets.ai_api_key:
            imported_secret_fields.append("ai_api_key")

        db.add(settings)
        db.add(ai_settings)
        await db.commit()
        await db.refresh(settings)
        await db.refresh(ai_settings)
    except Exception:
        await db.rollback()
        raise

    set_client_ip_header(settings.client_ip_header)
    apply_console_log_level(settings.log_level)
    if "panel_monitoring_enabled" in system_data:
        from services.panel_monitor import set_monitoring_enabled

        await set_monitoring_enabled(bool(settings.panel_monitoring_enabled))
    new_cache_policy = (
        settings.plugin_download_cache_path,
        settings.plugin_download_cache_max_age_days,
        settings.plugin_download_cache_max_megabytes,
    )
    if new_cache_policy != old_cache_policy:
        with suppress(OSError):
            prune_download_cache(CachePolicy.from_settings(settings))
    if "audit_log_retention_days" in system_data:
        await _apply_audit_retention_now()
    updated_fields = list(system_data) + list(body.ai.model_dump())
    await record_audit_event(
        category="settings",
        action="system.import",
        status="success",
        user=current_user,
        request=request,
        details={
            "updated_fields": updated_fields,
            "imported_secret_fields": imported_secret_fields,
        },
    )
    return SystemSettingsImportResult(
        success=True,
        message="System and AI settings imported successfully; Discord data was not included.",
        ai_enabled_without_key=ai_enabled_without_key,
        updated_fields=updated_fields,
        imported_secret_fields=imported_secret_fields,
        preserved_secret_fields=preserved_secret_fields,
    )


@router.get("", response_model=SystemSettingsView)
async def read_system_settings(db: DatabaseSession, current_user: AdminUser) -> SystemSettingsView:
    """Return panel settings with secrets replaced by presence flags."""
    settings = await SystemSettings.get_or_create_settings(db)
    return to_view(settings)


@router.put("", response_model=SystemSettingsView)
async def update_system_settings(
    patch: SystemSettingsPatch,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> SystemSettingsView:
    """Apply a partial settings update. Omitted secrets are left unchanged."""
    settings = await SystemSettings.get_or_create_settings(db)

    update_data = patch.model_dump(exclude_unset=True)
    clear_global_github_token = update_data.pop("clear_global_github_token", False)
    global_github_token = update_data.pop("global_github_token", None)
    smtp_password = update_data.pop("smtp_password", None)
    if update_data.get("panel_monitoring_enabled") is None:
        update_data.pop("panel_monitoring_enabled", None)
    settings.sqlmodel_update(update_data)

    if clear_global_github_token or (global_github_token and global_github_token.strip()):
        settings.github_token_fingerprint = None
        settings.github_token_verification = None

    if clear_global_github_token:
        settings.global_github_token = None
    elif global_github_token and global_github_token.strip():
        settings.global_github_token = global_github_token.strip()

    if smtp_password and smtp_password.strip():
        settings.smtp_password = smtp_password.strip()

    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    # Attribution and console verbosity must follow the policy saved just now.
    set_client_ip_header(settings.client_ip_header)
    apply_console_log_level(settings.log_level)
    if "panel_monitoring_enabled" in update_data:
        from services.panel_monitor import set_monitoring_enabled

        await set_monitoring_enabled(bool(settings.panel_monitoring_enabled))
    # A tightened retention limit should take effect now, not at the next download.
    if {
        "plugin_download_cache_path",
        "plugin_download_cache_max_age_days",
        "plugin_download_cache_max_megabytes",
    } & set(update_data):
        with suppress(OSError):
            prune_download_cache(CachePolicy.from_settings(settings))
    if "audit_log_retention_days" in update_data:
        await _apply_audit_retention_now()
    await record_audit_event(
        category="settings",
        action="system.update",
        status="success",
        user=current_user,
        request=request,
        details={
            "changed_fields": [
                field
                for field in update_data
                if field not in {"global_github_token", "smtp_password"}
            ]
            + (["global_github_token"] if clear_global_github_token or global_github_token else [])
            + (["smtp_password"] if smtp_password else [])
        },
    )
    return to_view(settings)


@router.post("/test-email", response_model=EmailTestResult)
async def send_test_email(
    body: EmailTestRequest,
    db: DatabaseSession,
    current_user: AdminUser,
) -> EmailTestResult:
    """Send a test message using the currently saved email configuration."""
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body>
        <p>This is a test email from CS2 Server Manager.</p>
        <p>If you're reading this, your email configuration is working correctly.</p>
        <p>Test initiated by: {current_user.username}</p>
    </body>
    </html>
    """
    text_content = (
        "This is a test email from CS2 Server Manager.\n"
        f"Test initiated by: {current_user.username}\n"
    )
    success = await email_service.send_email(
        db,
        body.test_email,
        "CS2 Server Manager - Email Test",
        html_content,
        text_content,
    )
    if success:
        return EmailTestResult(
            success=True,
            message=f"Test email sent successfully to {body.test_email}",
        )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to send test email. Please check your email configuration and server logs.",
    )


@router.put("/gmail/credentials", response_model=ActionResult)
async def put_gmail_credentials(
    body: GmailCredentialsUpload,
    db: DatabaseSession,
    current_user: AdminUser,
) -> ActionResult:
    """Store Google Cloud OAuth client JSON (write-only)."""
    result = await upload_gmail_credentials(
        GmailCredentialsUploadRequest(credentials_json=body.credentials_json),
        db,
        current_user,
    )
    return ActionResult(success=bool(result.get("success")), message=str(result.get("message", "")))


@router.get("/gmail/authorize", response_model=GmailAuthorizeResult)
async def start_gmail_authorize(
    request: Request,
    db: DatabaseSession,
    current_user: AdminUser,
) -> GmailAuthorizeResult:
    """Return the Google OAuth consent URL. Secrets stay on the server."""
    result = await gmail_oauth_authorize(request, db, current_user)
    return GmailAuthorizeResult(
        authorization_url=result["authorization_url"],
        state=result.get("state"),
    )


@router.delete("/gmail", response_model=ActionResult)
async def delete_gmail_authorization(
    db: DatabaseSession,
    current_user: AdminUser,
) -> ActionResult:
    """Clear the stored Gmail OAuth token (credentials JSON is kept)."""
    result = await revoke_gmail_authorization(db, current_user)
    return ActionResult(success=bool(result.get("success")), message=str(result.get("message", "")))


def _ai_view(payload) -> AssistantSystemSettingsView:
    return AssistantSystemSettingsView(
        enabled=bool(payload.enabled),
        base_url=payload.base_url,
        model=payload.model,
        api_protocol=payload.api_protocol,
        api_key_configured=bool(payload.api_key_configured),
        admin_prompt=payload.admin_prompt,
        private_endpoint_allowlist=list(getattr(payload, "private_endpoint_allowlist", None) or []),
        reasoning_effort=getattr(payload, "reasoning_effort", None),
        temperature=getattr(payload, "temperature", None),
        top_p=getattr(payload, "top_p", None),
        max_completion_tokens=int(getattr(payload, "max_completion_tokens", 2048) or 2048),
        token_limit_parameter=getattr(payload, "token_limit_parameter", None)
        or "max_completion_tokens",
        frequency_penalty=getattr(payload, "frequency_penalty", None),
        presence_penalty=getattr(payload, "presence_penalty", None),
        verbosity=getattr(payload, "verbosity", None),
        parallel_tool_calls=getattr(payload, "parallel_tool_calls", None),
        request_timeout_seconds=int(payload.request_timeout_seconds),
        history_retention_days=int(payload.history_retention_days),
        max_provider_rounds=int(payload.max_provider_rounds),
        max_tool_calls_per_round=int(payload.max_tool_calls_per_round),
        context_window_tokens=_context_window_tokens(
            getattr(payload, "context_window_tokens", 262_144)
        ),
        requests_per_minute=int(getattr(payload, "requests_per_minute", 60) or 60),
        provider_tested=bool(payload.provider_tested),
        tool_calling_tested=bool(payload.tool_calling_tested),
        streaming_tested=bool(payload.streaming_tested),
    )


@router.get("/ai", response_model=AssistantSystemSettingsView)
async def get_assistant_system_settings(
    db: DatabaseSession, current_user: AdminUser
) -> AssistantSystemSettingsView:
    return _ai_view(await legacy_ai.get_system_ai_settings(db, current_user))


@router.put("/ai", response_model=AssistantSystemSettingsView)
async def update_assistant_system_settings(
    body: AssistantSystemSettingsPatch,
    db: DatabaseSession,
    current_user: AdminUser,
) -> AssistantSystemSettingsView:
    return _ai_view(
        await legacy_ai.update_system_ai_settings(
            AISystemSettingsUpdate(**body.model_dump(exclude_unset=True)),
            db,
            current_user,
        )
    )


@router.post("/ai/test", response_model=AssistantProviderTestView)
async def test_assistant_system_settings(
    body: AssistantProviderTestBody,
    db: DatabaseSession,
    current_user: AdminUser,
) -> AssistantProviderTestView:
    payload = await legacy_ai.test_system_ai_settings(
        AIProviderTestRequest(**body.model_dump(exclude_unset=True)),
        db,
        current_user,
    )
    return AssistantProviderTestView(
        success=bool(payload.success),
        text_response_ok=bool(payload.text_response_ok),
        tool_calling_ok=bool(payload.tool_calling_ok),
        streaming_ok=bool(payload.streaming_ok),
        message=str(payload.message),
    )


@router.post("/test-github-token", response_model=GitHubTokenVerificationView)
async def test_github_token(current_user: AdminUser) -> GitHubTokenVerificationView:
    result = await verify_token(current_user.id)
    return GitHubTokenVerificationView(**result.model_dump())


@router.post("/plugin-download-cache/clear", response_model=ActionResult)
async def clear_plugin_download_cache(db: DatabaseSession, current_user: AdminUser) -> ActionResult:
    settings = await SystemSettings.get_or_create_settings(db)
    count = clear_download_cache(settings.plugin_download_cache_path)
    return ActionResult(success=True, message=f"Cleared {count} cached files")


@router.post("/plugin-download-cache/prune", response_model=ActionResult)
async def prune_plugin_download_cache(db: DatabaseSession, current_user: AdminUser) -> ActionResult:
    """Apply the saved retention policy now instead of waiting for a download."""
    settings = await SystemSettings.get_or_create_settings(db)
    removed, freed = prune_download_cache(CachePolicy.from_settings(settings))
    return ActionResult(
        success=True,
        message=f"Removed {removed} expired cached files ({freed / (1024 * 1024):.1f} MB)",
    )
