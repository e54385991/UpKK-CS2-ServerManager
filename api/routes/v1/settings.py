"""Versioned admin system-settings endpoints (non-secret projections)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status

from api.dependencies import AdminUser, DatabaseSession
from api.routes import ai as legacy_ai
from api.routes.gmail_oauth import (
    gmail_oauth_authorize,
    revoke_gmail_authorization,
    upload_gmail_credentials,
)
from modules import GmailCredentialsUploadRequest, SystemSettings
from modules.schemas.ai import AIProviderTestRequest, AISystemSettingsUpdate
from services.audit_log_service import record_audit_event
from services.client_ip import set_client_ip_header
from services.email_service import email_service
from services.log_output import apply_console_log_level, effective_console_log_level

from .schemas import (
    ActionResult,
    AssistantProviderTestBody,
    AssistantProviderTestView,
    AssistantSystemSettingsPatch,
    AssistantSystemSettingsView,
    EmailTestRequest,
    EmailTestResult,
    GmailAuthorizeResult,
    GmailCredentialsUpload,
    LogLevel,
    SystemSettingsPatch,
    SystemSettingsView,
)

router = APIRouter(prefix="/api/v1/settings", tags=["v1-settings"])


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


def to_view(settings: SystemSettings) -> SystemSettingsView:
    """Project the ORM row to the browser-facing, non-secret view."""
    has_gmail_credentials = bool((settings.gmail_credentials_json or "").strip())
    has_gmail_token = bool((settings.gmail_token_json or "").strip())
    provider: Literal["gmail", "smtp"] = "gmail" if settings.email_provider == "gmail" else "smtp"
    if settings.default_proxy_mode == "direct":
        proxy_mode: Literal["direct", "panel", "github_url"] = "direct"
    elif settings.default_proxy_mode == "github_url":
        proxy_mode = "github_url"
    else:
        proxy_mode = "panel"
    return SystemSettingsView(
        default_proxy_mode=proxy_mode,
        github_proxy_url=settings.github_proxy_url,
        captcha_enabled=bool(settings.captcha_enabled),
        client_ip_header=settings.client_ip_header,
        log_level=_log_level(settings.log_level),
        effective_log_level=_log_level(effective_console_log_level(settings.log_level)) or "INFO",
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
    settings.sqlmodel_update(update_data)

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
