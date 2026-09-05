"""AI provider settings, conversations, runs, approvals, and events."""

from __future__ import annotations

from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    status,
)
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.dependencies import ActiveUser, AdminUser, DatabaseSession
from modules import (
    AIConversation,
    AIConversationCreate,
    AIConversationDetail,
    AIConversationResponse,
    AIMessage,
    AIMessageCreate,
    AIMessageResponse,
    AIProviderTestRequest,
    AIProviderTestResponse,
    AIRun,
    AIRunResponse,
    AISystemSettings,
    AISystemSettingsResponse,
    AISystemSettingsUpdate,
    AIToolDecisionRequest,
    AIToolRun,
    AIToolRunResponse,
    Server,
    User,
    UserAISettingsResponse,
    UserAISettingsUpdate,
)
from modules.schemas.ai import AIBackgroundTaskResponse, AIBackgroundTaskToolResponse
from modules.utils import get_current_time
from services.agent_policy_service import (
    AgentCapabilityDenied,
    get_effective_agent_policy,
    require_agent_capabilities,
)
from services.ai_access import audit_security_event
from services.ai_orchestrator import (
    ACTIVE_RUN_STATUSES,
    cleanup_expired_ai_runs,
    interrupt_conversation_run,
    process_ai_run,
    reconcile_stale_ai_server_lock,
    reconcile_waiting_approval_runs,
)
from services.ai_provider import test_provider
from services.ai_security import (
    AIConfigurationError,
    AIProviderConfig,
    credential_encryption_available,  # noqa: F401
    decrypt_credential,
    encrypt_credential,
    get_effective_provider,
    normalize_base_url,
)
from services.task_registry import ai_task_registry

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

router = APIRouter(tags=["ai-assistant"])


@router.get("/api/system/ai-settings", response_model=AISystemSettingsResponse)
async def get_system_ai_settings(
    db: DatabaseSession,
    current_user: AdminUser,
) -> AISystemSettingsResponse:
    return _system_response(await AISystemSettings.get_or_create(db))


@router.put("/api/system/ai-settings", response_model=AISystemSettingsResponse)
async def update_system_ai_settings(
    request: AISystemSettingsUpdate,
    db: DatabaseSession,
    current_user: AdminUser,
) -> AISystemSettingsResponse:
    item = await AISystemSettings.get_or_create(db)
    changed_provider = _apply_system_provider_fields(item, request)
    _apply_system_runtime_limits(item, request)
    if changed_provider:
        item.provider_tested = False
        item.tool_calling_tested = False
        item.streaming_tested = False
    _apply_system_enabled(item, request)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _system_response(item)


@router.post("/api/system/ai-settings/test", response_model=AIProviderTestResponse)
async def test_system_ai_settings(
    request: AIProviderTestRequest,
    db: DatabaseSession,
    current_user: AdminUser,
) -> AIProviderTestResponse:
    item = await AISystemSettings.get_or_create(db)
    try:
        base_url = normalize_base_url(request.base_url or item.base_url or "")
        model = (request.model or item.model or "").strip()
        api_key = request.api_key or decrypt_credential(item.api_key_encrypted)
        if not model or not api_key:
            raise AIConfigurationError("Base URL, model, and API key are required")
        candidate = AIProviderConfig(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=item.request_timeout_seconds,
            allowlist=tuple(item.private_endpoint_allowlist or []),
            source="global",
            api_protocol=request.api_protocol or item.api_protocol,
            admin_prompt=item.admin_prompt or "",
            context_window_tokens=getattr(item, "context_window_tokens", 262_144),
            **_test_model_parameters(request, item),
        )
        text_ok, tool_ok, streaming_ok, message = await test_provider(candidate)
    except (AIConfigurationError, ValueError) as exc:
        text_ok, tool_ok, streaming_ok, message = False, False, False, str(exc)
    if _is_saved_provider_test(request, item):
        _apply_saved_provider_test_flags(
            item,
            text_ok=text_ok,
            tool_ok=tool_ok,
            streaming_ok=streaming_ok,
        )
        db.add(item)
        await db.commit()
    return AIProviderTestResponse(
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
    return await _user_response(db, current_user, await _get_user_settings(db, current_user.id))


@router.put("/api/auth/ai-settings", response_model=UserAISettingsResponse)
async def update_user_ai_settings(
    request: UserAISettingsUpdate,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> UserAISettingsResponse:
    item = await _get_user_settings(db, current_user.id)
    changed_provider = request.mode != item.mode
    item.mode = request.mode
    try:
        if "base_url" in request.model_fields_set:
            normalized = normalize_base_url(request.base_url) if request.base_url else None
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
            item.api_key_encrypted = encrypt_credential(request.api_key)
            changed_provider = True
        elif request.clear_api_key:
            item.api_key_encrypted = None
            changed_provider = True
        changed_provider |= _apply_model_parameters(request, item)
    except (AIConfigurationError, ValueError) as exc:
        raise _configuration_error(exc) from exc
    if changed_provider:
        item.provider_tested = False
        item.tool_calling_tested = False
        item.streaming_tested = False
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return await _user_response(db, current_user, item)


@router.post("/api/auth/ai-settings/test", response_model=AIProviderTestResponse)
async def test_user_ai_settings(
    request: AIProviderTestRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AIProviderTestResponse:
    item = await _get_user_settings(db, current_user.id)
    system = await AISystemSettings.get_or_create(db)
    if item.mode != "custom":
        return AIProviderTestResponse(
            success=False,
            text_response_ok=False,
            tool_calling_ok=False,
            streaming_ok=False,
            message="Switch to a custom provider before testing personal settings",
        )
    try:
        base_url = normalize_base_url(request.base_url or item.base_url or "")
        model = (request.model or item.model or "").strip()
        api_key = request.api_key or decrypt_credential(item.api_key_encrypted)
        if not model or not api_key:
            raise AIConfigurationError("Base URL, model, and API key are required")
        candidate = AIProviderConfig(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=system.request_timeout_seconds,
            allowlist=tuple(system.private_endpoint_allowlist or []),
            source="custom",
            api_protocol=request.api_protocol or item.api_protocol,
            admin_prompt=system.admin_prompt or "",
            context_window_tokens=getattr(system, "context_window_tokens", 262_144),
            **_test_model_parameters(request, item),
        )
        text_ok, tool_ok, streaming_ok, message = await test_provider(candidate)
    except (AIConfigurationError, ValueError) as exc:
        text_ok, tool_ok, streaming_ok, message = False, False, False, str(exc)
    if _is_saved_provider_test(request, item):
        _apply_saved_provider_test_flags(
            item,
            text_ok=text_ok,
            tool_ok=tool_ok,
            streaming_ok=streaming_ok,
        )
        db.add(item)
        await db.commit()
    return AIProviderTestResponse(
        success=text_ok and tool_ok and streaming_ok,
        text_response_ok=text_ok,
        tool_calling_ok=tool_ok,
        streaming_ok=streaming_ok,
        message=message,
    )


async def _server_for_user(db: AsyncSession, user: User, server_id: int) -> Server:
    server = (
        await Server.get_by_id(db, server_id)
        if user.is_admin
        else await Server.get_by_id_and_user(db, server_id, user.id)
    )
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return server


async def _conversation_for_user(
    db: AsyncSession, user: User, conversation_id: str
) -> AIConversation:
    result = await db.execute(
        select(AIConversation).where(
            AIConversation.id == conversation_id,
            AIConversation.user_id == user.id,
            AIConversation.source == "web",
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


async def _require_enabled_provider(db: DatabaseSession, user) -> None:
    """Fail with a conflict the caller can act on, never a 500.

    A stored API key that no longer decrypts (AI_CREDENTIAL_ENCRYPTION_KEY was
    rotated after it was saved) is a configuration problem, so surface its
    remediation message instead of letting it escape as a server error.
    """
    try:
        provider = await get_effective_provider(db, user)
    except AIConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No AI provider is enabled",
        )


@router.post("/api/ai/conversations", response_model=AIConversationResponse)
async def create_ai_conversation(
    request: AIConversationCreate,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AIConversation:
    await _require_enabled_provider(db, current_user)
    if request.server_id is not None:
        await _server_for_user(db, current_user, request.server_id)
        policy = await get_effective_agent_policy(db, request.server_id)
        if not policy.enabled:
            raise HTTPException(status_code=403, detail="AI Agent is disabled for this server")
        await reconcile_waiting_approval_runs(db, user_id=current_user.id)
        await reconcile_stale_ai_server_lock(db, request.server_id)
    item = AIConversation(
        user_id=current_user.id,
        server_id=request.server_id,
        title=(request.title or "New conversation").strip() or "New conversation",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/api/ai/conversations", response_model=list[AIConversationResponse])
async def list_ai_conversations(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> list[AIConversation]:
    result = await db.execute(
        select(AIConversation)
        .where(AIConversation.user_id == current_user.id, AIConversation.source == "web")
        .order_by(col(AIConversation.updated_at).desc())
        .limit(100)
    )
    return list(result.scalars().all())


@router.get("/api/ai/conversations/{conversation_id}", response_model=AIConversationDetail)
async def get_ai_conversation(
    conversation_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AIConversationDetail:
    conversation = await _conversation_for_user(db, current_user, conversation_id)
    result = await db.execute(
        select(AIMessage)
        .where(
            AIMessage.conversation_id == conversation.id,
            col(AIMessage.visible).is_(True),
        )
        .order_by(col(AIMessage.id).asc())
    )
    return AIConversationDetail(
        **AIConversationResponse.model_validate(conversation).model_dump(),
        messages=[AIMessageResponse.model_validate(item) for item in result.scalars().all()],
    )


@router.delete("/api/ai/conversations/{conversation_id}", status_code=204)
async def delete_ai_conversation(
    conversation_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> None:
    conversation = await _conversation_for_user(db, current_user, conversation_id)
    await reconcile_waiting_approval_runs(db, conversation_id=conversation.id)
    active_result = await db.execute(
        select(func.count())
        .select_from(AIRun)
        .where(
            AIRun.conversation_id == conversation.id,
            col(AIRun.status).in_(ACTIVE_RUN_STATUSES),
        )
    )
    if int(active_result.scalar_one()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a conversation with an active run",
        )
    await db.delete(conversation)
    await db.commit()


@router.post(
    "/api/ai/conversations/{conversation_id}/messages",
    response_model=AIRunResponse,
)
async def send_ai_message(
    conversation_id: str,
    request: AIMessageCreate,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AIRun:
    conversation = await _conversation_for_user(db, current_user, conversation_id)
    await _require_enabled_provider(db, current_user)
    if conversation.server_id is not None:
        await _server_for_user(db, current_user, conversation.server_id)
        policy = await get_effective_agent_policy(db, conversation.server_id)
        if not policy.enabled:
            raise HTTPException(status_code=403, detail="AI Agent is disabled for this server")
    await reconcile_waiting_approval_runs(db, conversation_id=conversation.id)
    if conversation.server_id is not None:
        await reconcile_stale_ai_server_lock(db, conversation.server_id)
    active_result = await db.execute(
        select(func.count())
        .select_from(AIRun)
        .where(
            AIRun.conversation_id == conversation.id,
            col(AIRun.status).in_(ACTIVE_RUN_STATUSES),
        )
    )
    if int(active_result.scalar_one()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This conversation already has an active run",
        )
    message = AIMessage(
        conversation_id=conversation.id,
        role="user",
        content=request.content,
        visible=True,
    )
    run = AIRun(
        conversation_id=conversation.id,
        user_id=current_user.id,
        server_id=conversation.server_id,
        status="queued",
        source="web",
    )
    if conversation.title == "New conversation":
        conversation.title = request.content[:80]
    conversation.updated_at = get_current_time()
    db.add(message)
    db.add(run)
    db.add(conversation)
    await db.commit()
    await db.refresh(run)
    ai_task_registry.create(process_ai_run(run.id))
    return run


@router.post("/api/ai/conversations/{conversation_id}/interrupt")
async def interrupt_conversation(
    conversation_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    conversation = await _conversation_for_user(db, current_user, conversation_id)
    try:
        result = await interrupt_conversation_run(db, current_user, conversation.id)
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _run_for_user(db: AsyncSession, user: User, run_id: str) -> AIRun:
    result = await db.execute(
        select(AIRun).where(
            AIRun.id == run_id,
            AIRun.user_id == user.id,
            AIRun.source == "web",
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.get("/api/ai/runs/{run_id}")
async def get_ai_run(
    run_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, Any]:
    run = await _run_for_user(db, current_user, run_id)
    await reconcile_waiting_approval_runs(db, run_id=run.id)
    result = await db.execute(
        select(AIToolRun)
        .where(AIToolRun.run_id == run.id)
        .order_by(col(AIToolRun.created_at).asc(), col(AIToolRun.id).asc())
    )
    return {
        **AIRunResponse.model_validate(run).model_dump(mode="json"),
        "tools": [
            AIToolRunResponse.model_validate(item).model_dump(mode="json")
            for item in result.scalars().all()
        ],
    }


@router.get(
    "/api/ai/tasks",
    response_model=list[AIBackgroundTaskResponse],
    name="list_ai_background_tasks",
)
async def _list_ai_background_tasks_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    conversation_id: str = Query(min_length=36, max_length=36),
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> list[AIBackgroundTaskResponse]:
    """Return active and recent AI tasks belonging to one caller conversation."""
    return await _list_ai_background_tasks(db, current_user, limit, conversation_id)


async def _list_ai_background_tasks(
    db: DatabaseSession,
    current_user: ActiveUser,
    limit: int,
    conversation_id: str,
) -> list[AIBackgroundTaskResponse]:
    """Load task rows and map only write-tool progress to public DTOs."""
    await reconcile_waiting_approval_runs(db, user_id=current_user.id)
    await cleanup_expired_ai_runs(db, user_id=current_user.id)
    run_result = await db.execute(
        select(AIRun)
        .join(AIToolRun, col(AIToolRun.run_id) == col(AIRun.id))
        .where(
            AIRun.user_id == current_user.id,
            AIRun.conversation_id == conversation_id,
            AIRun.source == "web",
            AIToolRun.risk == "write",
        )
        .distinct()
        .order_by(col(AIRun.updated_at).desc(), col(AIRun.created_at).desc())
        .limit(limit)
    )
    runs = list(run_result.scalars().all())
    if not runs:
        return []
    run_ids = [run.id for run in runs]
    tool_result = await db.execute(
        select(AIToolRun)
        .where(
            col(AIToolRun.run_id).in_(run_ids),
            AIToolRun.risk == "write",
        )
        .order_by(col(AIToolRun.created_at).asc(), col(AIToolRun.id).asc())
    )
    tools_by_run: dict[str, list[AIBackgroundTaskToolResponse]] = {}
    for tool in tool_result.scalars().all():
        if tool.risk != "write":
            continue
        tools_by_run.setdefault(tool.run_id, []).append(
            AIBackgroundTaskToolResponse(
                id=tool.id,
                tool_name=tool.tool_name,
                risk=tool.risk,
                status=tool.status,
                plan_snapshot=getattr(tool, "plan_snapshot", None),
                progress_snapshot=getattr(tool, "progress_snapshot", None),
                progress_updated_at=getattr(tool, "progress_updated_at", None),
                error=tool.error,
                created_at=tool.created_at,
                completed_at=tool.completed_at,
            )
        )
    return [
        AIBackgroundTaskResponse(
            id=run.id,
            conversation_id=run.conversation_id,
            server_id=run.server_id,
            status=run.status,
            error=run.error,
            created_at=run.created_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
            tools=tools_by_run.get(run.id, []),
        )
        for run in runs
        if run.id in tools_by_run
    ]


# Keep the old Python-call signature available to integrations and tests. The
# decorated endpoint above retains the stable OpenAPI operation name.
async def list_ai_background_tasks(
    limit: int,
    conversation_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> list[AIBackgroundTaskResponse]:
    return await _list_ai_background_tasks(db, current_user, limit, conversation_id)


@router.delete("/api/ai/tasks/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_background_task(
    run_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> None:
    run = await _run_for_user(db, current_user, run_id)
    if run.status in ACTIVE_RUN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active AI tasks cannot be deleted",
        )
    await db.delete(run)
    await db.commit()


@router.post("/api/ai/runs/{run_id}/tools/{tool_run_id}")
async def decide_ai_tool(
    run_id: str,
    tool_run_id: str,
    request: AIToolDecisionRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict[str, str]:
    run = await _run_for_user(db, current_user, run_id)
    terminal_runs = await reconcile_waiting_approval_runs(db, run_id=run.id)
    if run.id in terminal_runs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tool approval expired or was cancelled; request a fresh plan",
        )
    item_result = await db.execute(
        select(AIToolRun).where(AIToolRun.id == tool_run_id, AIToolRun.run_id == run.id)
    )
    item = item_result.scalar_one_or_none()
    if (
        item is None
        or item.run_id != run.id
        or item.status != "pending_approval"
        or not item.requires_approval
    ):
        audit_security_event(
            "approval_not_pending",
            user_id=current_user.id,
            server_id=run.server_id,
            operation=item.tool_name if item is not None else None,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tool approval is no longer pending",
        )
    if item.arguments_hash != request.arguments_hash:
        audit_security_event(
            "approval_arguments_mismatch",
            user_id=current_user.id,
            server_id=run.server_id,
            operation=item.tool_name,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tool arguments changed; reload the approval card",
        )
    approval_now = get_current_time()
    if item.approval_expires_at is not None and item.approval_expires_at.tzinfo is None:
        approval_now = approval_now.replace(tzinfo=None)
    if item.approval_expires_at is None or item.approval_expires_at <= approval_now:
        audit_security_event(
            "approval_expired",
            user_id=current_user.id,
            server_id=run.server_id,
            operation=item.tool_name,
        )
        await reconcile_waiting_approval_runs(db, run_id=run.id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tool approval expired; request a fresh plan",
        )
    if run.server_id is not None:
        await _server_for_user(db, current_user, run.server_id)
        if request.decision == "approve":
            from services.ai_tools import TOOLS_BY_NAME

            spec = TOOLS_BY_NAME.get(item.tool_name)
            if spec is None:
                raise HTTPException(status_code=409, detail="Tool is no longer available")
            try:
                await require_agent_capabilities(
                    db,
                    run.server_id,
                    spec.required_capabilities(item.arguments),
                )
            except AgentCapabilityDenied as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
    item.status = (
        "queued"
        if request.decision == "approve" and item.risk == "write"
        else "approved"
        if request.decision == "approve"
        else "rejected"
    )
    item.approved_by = current_user.id
    item.approved_actor_type = "web"
    item.approved_at = get_current_time()
    db.add(item)
    await db.commit()

    pending_result = await db.execute(
        select(func.count())
        .select_from(AIToolRun)
        .where(
            AIToolRun.run_id == run.id,
            AIToolRun.status == "pending_approval",
        )
    )
    if not int(pending_result.scalar_one()):
        ai_task_registry.create(process_ai_run(run.id))
    return {"status": item.status}


from . import ai_stream_routes as _ai_stream_routes  # noqa: E402,F401
from .ai_stream_routes import (  # noqa: E402,F401
    _encode_sse_event,
    ai_run_event_stream,
    ai_run_events,
)
