"""Focused route implementation; the original module retains patchable dependencies."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import ActiveUser, AdminUser, DatabaseSession
from modules import (
    AIConversation,
    AIProviderTestRequest,
    AIProviderTestResponse,
    AISystemSettingsResponse,
    AISystemSettingsUpdate,
    Server,
    User,
    UserAISettingsResponse,
    UserAISettingsUpdate,
)
from services.ai_security import (
    credential_encryption_available,  # noqa: F401
)
from services.compat import LateBoundModule

from .ai_helpers import (  # noqa: F401
    _MODEL_PARAMETER_NAMES,
    _apply_model_parameters,
    _apply_saved_provider_test_flags,
    _apply_system_enabled,
    _apply_system_provider_fields,
    _apply_system_runtime_limits,
    _configuration_error,
    _get_user_settings,
    _is_saved_provider_test,
    _system_ready_to_enable,
    _system_response,
    _test_model_parameters,
    _user_response,
)

host = LateBoundModule("api.routes.ai")
router = host.router


@router.put("/api/system/ai-settings", response_model=AISystemSettingsResponse)
async def update_system_ai_settings(
    request: AISystemSettingsUpdate,
    db: DatabaseSession,
    current_user: AdminUser,
) -> AISystemSettingsResponse:
    item = await host.AISystemSettings.get_or_create(db)
    changed_provider = host._apply_system_provider_fields(item, request)
    host._apply_system_runtime_limits(item, request)
    if changed_provider:
        item.provider_tested = False
        item.tool_calling_tested = False
        item.streaming_tested = False
    host._apply_system_enabled(item, request)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return host._system_response(item)


@router.post("/api/system/ai-settings/test", response_model=AIProviderTestResponse)
async def test_system_ai_settings(
    request: AIProviderTestRequest,
    db: DatabaseSession,
    current_user: AdminUser,
) -> AIProviderTestResponse:
    item = await host.AISystemSettings.get_or_create(db)
    try:
        base_url = host.normalize_base_url(request.base_url or item.base_url or "")
        model = (request.model or item.model or "").strip()
        api_key = request.api_key or host.decrypt_credential(item.api_key_encrypted)
        if not model or not api_key:
            raise host.AIConfigurationError("Base URL, model, and API key are required")
        candidate = host.AIProviderConfig(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=item.request_timeout_seconds,
            allowlist=tuple(item.private_endpoint_allowlist or []),
            source="global",
            api_protocol=request.api_protocol or item.api_protocol,
            admin_prompt=item.admin_prompt or "",
            context_window_tokens=getattr(item, "context_window_tokens", 262_144),
            requests_per_minute=getattr(item, "requests_per_minute", 60),
            **host._test_model_parameters(request, item),
        )
        # RPM waits and provider I/O must not keep a database transaction open.
        await db.commit()
        text_ok, tool_ok, streaming_ok, message = await host.test_provider(candidate)
    except (host.AIConfigurationError, ValueError) as exc:
        text_ok, tool_ok, streaming_ok, message = False, False, False, str(exc)
    if host._is_saved_provider_test(request, item):
        host._apply_saved_provider_test_flags(
            item,
            text_ok=text_ok,
            tool_ok=tool_ok,
            streaming_ok=streaming_ok,
        )
        db.add(item)
        await db.commit()
    return host.AIProviderTestResponse(
        success=text_ok and tool_ok and streaming_ok,
        text_response_ok=text_ok,
        tool_calling_ok=tool_ok,
        streaming_ok=streaming_ok,
        message=message,
    )


@router.get("/api/auth/ai-settings", response_model=UserAISettingsResponse)
async def get_user_ai_settings(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> UserAISettingsResponse:
    return await host._user_response(
        db, current_user, await host._get_user_settings(db, current_user.id)
    )


@router.put("/api/auth/ai-settings", response_model=UserAISettingsResponse)
async def update_user_ai_settings(
    request: UserAISettingsUpdate,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> UserAISettingsResponse:
    item = await host._get_user_settings(db, current_user.id)
    changed_provider = request.mode != item.mode
    item.mode = request.mode
    try:
        if "base_url" in request.model_fields_set:
            normalized = host.normalize_base_url(request.base_url) if request.base_url else None
            changed_provider |= normalized != item.base_url
            item.base_url = normalized
        if "model" in request.model_fields_set:
            model = (request.model or "").strip() or None
            changed_provider |= model != item.model
            item.model = model
        if "api_protocol" in request.model_fields_set and request.api_protocol is not None:
            changed_provider |= request.api_protocol != item.api_protocol
            item.api_protocol = request.api_protocol
        if request.api_key:
            item.api_key_encrypted = host.encrypt_credential(request.api_key)
            changed_provider = True
        elif request.clear_api_key:
            item.api_key_encrypted = None
            changed_provider = True
        changed_provider |= host._apply_model_parameters(request, item)
    except (host.AIConfigurationError, ValueError) as exc:
        raise host._configuration_error(exc) from exc
    if changed_provider:
        item.provider_tested = False
        item.tool_calling_tested = False
        item.streaming_tested = False
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return await host._user_response(db, current_user, item)


@router.post("/api/auth/ai-settings/test", response_model=AIProviderTestResponse)
async def test_user_ai_settings(
    request: AIProviderTestRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AIProviderTestResponse:
    item = await host._get_user_settings(db, current_user.id)
    system = await host.AISystemSettings.get_or_create(db)
    if item.mode != "custom":
        return host.AIProviderTestResponse(
            success=False,
            text_response_ok=False,
            tool_calling_ok=False,
            streaming_ok=False,
            message="Switch to a custom provider before testing personal settings",
        )
    try:
        base_url = host.normalize_base_url(request.base_url or item.base_url or "")
        model = (request.model or item.model or "").strip()
        api_key = request.api_key or host.decrypt_credential(item.api_key_encrypted)
        if not model or not api_key:
            raise host.AIConfigurationError("Base URL, model, and API key are required")
        candidate = host.AIProviderConfig(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=system.request_timeout_seconds,
            allowlist=tuple(system.private_endpoint_allowlist or []),
            source="custom",
            api_protocol=request.api_protocol or item.api_protocol,
            admin_prompt=system.admin_prompt or "",
            context_window_tokens=getattr(system, "context_window_tokens", 262_144),
            requests_per_minute=getattr(system, "requests_per_minute", 60),
            **host._test_model_parameters(request, item),
        )
        # RPM waits and provider I/O must not keep a database transaction open.
        await db.commit()
        text_ok, tool_ok, streaming_ok, message = await host.test_provider(candidate)
    except (host.AIConfigurationError, ValueError) as exc:
        text_ok, tool_ok, streaming_ok, message = False, False, False, str(exc)
    if host._is_saved_provider_test(request, item):
        host._apply_saved_provider_test_flags(
            item,
            text_ok=text_ok,
            tool_ok=tool_ok,
            streaming_ok=streaming_ok,
        )
        db.add(item)
        await db.commit()
    return host.AIProviderTestResponse(
        success=text_ok and tool_ok and streaming_ok,
        text_response_ok=text_ok,
        tool_calling_ok=tool_ok,
        streaming_ok=streaming_ok,
        message=message,
    )


async def _server_for_user(db: AsyncSession, user: User, server_id: int) -> Server:
    server = (
        await host.Server.get_by_id(db, server_id)
        if user.is_admin
        else await host.Server.get_by_id_and_user(db, server_id, user.id)
    )
    if server is None:
        raise host.HTTPException(
            status_code=host.status.HTTP_404_NOT_FOUND, detail="Server not found"
        )
    return server


async def _conversation_for_user(
    db: AsyncSession, user: User, conversation_id: str
) -> AIConversation:
    result = await db.execute(
        host.select(host.AIConversation).where(
            host.AIConversation.id == conversation_id,
            host.AIConversation.user_id == user.id,
            host.AIConversation.source == "web",
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise host.HTTPException(
            status_code=host.status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return conversation
