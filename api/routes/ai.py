"""AI provider settings, conversations, runs, approvals, and events."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

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
    UserAISettings,
    UserAISettingsResponse,
    UserAISettingsUpdate,
    async_session_maker,
    authenticate_websocket,
    get_current_active_user,
    get_current_admin_user,
    get_db,
)
from modules.schemas.ai import AIBackgroundTaskResponse, AIBackgroundTaskToolResponse
from modules.utils import get_current_time
from services.ai_access import audit_security_event
from services.ai_events import ai_event_hub
from services.ai_orchestrator import (
    ACTIVE_RUN_STATUSES,
    interrupt_conversation_run,
    process_ai_run,
    reconcile_waiting_approval_runs,
)
from services.ai_provider import test_provider
from services.ai_security import (
    AIConfigurationError,
    AIProviderConfig,
    credential_encryption_available,
    decrypt_credential,
    encrypt_credential,
    get_effective_provider,
    normalize_allowlist,
    normalize_base_url,
)
from services.task_registry import ai_task_registry

router = APIRouter(tags=["ai-assistant"])

_MODEL_PARAMETER_NAMES = (
    "reasoning_effort",
    "temperature",
    "top_p",
    "max_completion_tokens",
    "token_limit_parameter",
    "frequency_penalty",
    "presence_penalty",
    "verbosity",
    "parallel_tool_calls",
)


def _model_parameters(item: AISystemSettings | UserAISettings) -> dict[str, Any]:
    return {name: getattr(item, name) for name in _MODEL_PARAMETER_NAMES}


def _validate_sampling_parameters(parameters: dict[str, Any]) -> None:
    if parameters["temperature"] is not None and parameters["top_p"] is not None:
        raise AIConfigurationError("Set temperature or top_p, not both")


def _apply_model_parameters(
    request: AISystemSettingsUpdate | UserAISettingsUpdate,
    item: AISystemSettings | UserAISettings,
) -> bool:
    parameters = _model_parameters(item)
    for name in _MODEL_PARAMETER_NAMES:
        if name in request.model_fields_set:
            value = getattr(request, name)
            if name == "max_completion_tokens" and value is None:
                value = 2048
            elif name == "token_limit_parameter" and value is None:
                value = "max_completion_tokens"
            parameters[name] = value
    _validate_sampling_parameters(parameters)
    changed = False
    for name, value in parameters.items():
        changed |= value != getattr(item, name)
        setattr(item, name, value)
    return changed


def _test_model_parameters(
    request: AIProviderTestRequest,
    item: AISystemSettings | UserAISettings,
) -> dict[str, Any]:
    parameters = _model_parameters(item)
    for name in _MODEL_PARAMETER_NAMES:
        if name in request.model_fields_set and getattr(request, name) is not None:
            parameters[name] = getattr(request, name)
    _validate_sampling_parameters(parameters)
    return parameters


def _system_response(item: AISystemSettings) -> AISystemSettingsResponse:
    return AISystemSettingsResponse(
        enabled=item.enabled,
        base_url=item.base_url,
        model=item.model,
        api_key_configured=bool(item.api_key_encrypted),
        admin_prompt=item.admin_prompt,
        private_endpoint_allowlist=item.private_endpoint_allowlist or [],
        **_model_parameters(item),
        request_timeout_seconds=item.request_timeout_seconds,
        history_retention_days=item.history_retention_days,
        max_provider_rounds=item.max_provider_rounds,
        provider_tested=item.provider_tested,
        tool_calling_tested=item.tool_calling_tested,
        streaming_tested=item.streaming_tested,
    )


async def _user_response(
    db: AsyncSession, user: User, item: UserAISettings
) -> UserAISettingsResponse:
    source = "none"
    enabled = False
    try:
        effective = await get_effective_provider(db, user)
    except AIConfigurationError:
        effective = None
    if effective is not None:
        source = effective.source
        enabled = True
    return UserAISettingsResponse(
        mode=item.mode,
        base_url=item.base_url,
        model=item.model,
        api_key_configured=bool(item.api_key_encrypted),
        **_model_parameters(item),
        provider_tested=item.provider_tested,
        tool_calling_tested=item.tool_calling_tested,
        streaming_tested=item.streaming_tested,
        effective_enabled=enabled,
        effective_source=source,
    )


async def _get_user_settings(db: AsyncSession, user_id: int) -> UserAISettings:
    item = await db.get(UserAISettings, user_id)
    if item is None:
        item = UserAISettings(user_id=user_id)
        db.add(item)
        await db.commit()
        await db.refresh(item)
    return item


def _configuration_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/api/system/ai-settings", response_model=AISystemSettingsResponse)
async def get_system_ai_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
) -> AISystemSettingsResponse:
    return _system_response(await AISystemSettings.get_or_create(db))


@router.put("/api/system/ai-settings", response_model=AISystemSettingsResponse)
async def update_system_ai_settings(
    request: AISystemSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
) -> AISystemSettingsResponse:
    item = await AISystemSettings.get_or_create(db)
    changed_provider = False
    try:
        if "base_url" in request.model_fields_set:
            normalized = normalize_base_url(request.base_url) if request.base_url else None
            changed_provider |= normalized != item.base_url
            item.base_url = normalized
        if "model" in request.model_fields_set:
            model = (request.model or "").strip() or None
            changed_provider |= model != item.model
            item.model = model
        if request.api_key:
            item.api_key_encrypted = encrypt_credential(request.api_key)
            changed_provider = True
        elif request.clear_api_key:
            item.api_key_encrypted = None
            changed_provider = True
        if request.private_endpoint_allowlist is not None:
            allowlist = normalize_allowlist(request.private_endpoint_allowlist)
            changed_provider |= allowlist != item.private_endpoint_allowlist
            item.private_endpoint_allowlist = allowlist
        changed_provider |= _apply_model_parameters(request, item)
    except (AIConfigurationError, ValueError) as exc:
        raise _configuration_error(exc) from exc

    if request.admin_prompt is not None:
        item.admin_prompt = request.admin_prompt.strip() or None
    if request.request_timeout_seconds is not None:
        item.request_timeout_seconds = request.request_timeout_seconds
    if request.max_provider_rounds is not None:
        item.max_provider_rounds = request.max_provider_rounds
    if request.history_retention_days is not None:
        item.history_retention_days = request.history_retention_days
    if changed_provider:
        item.provider_tested = False
        item.tool_calling_tested = False
        item.streaming_tested = False
        item.enabled = False
    if request.enabled is not None:
        if request.enabled and changed_provider:
            # Saving a changed endpoint always disables execution until the
            # new provider passes both tests. The requested enabled flag is
            # intentionally ignored for this one update.
            item.enabled = False
        elif request.enabled and not (
            item.base_url
            and item.model
            and item.api_key_encrypted
            and item.provider_tested
            and item.tool_calling_tested
            and item.streaming_tested
            and credential_encryption_available()
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Configure encryption, URL, model, and API key, then pass both "
                    "provider tests before enabling AI"
                ),
            )
        else:
            item.enabled = request.enabled
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _system_response(item)


@router.post("/api/system/ai-settings/test", response_model=AIProviderTestResponse)
async def test_system_ai_settings(
    request: AIProviderTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
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
            admin_prompt=item.admin_prompt or "",
            **_test_model_parameters(request, item),
        )
        text_ok, tool_ok, streaming_ok, message = await test_provider(candidate)
    except (AIConfigurationError, ValueError) as exc:
        text_ok, tool_ok, streaming_ok, message = False, False, False, str(exc)
    saved_configuration = not request.model_fields_set
    if saved_configuration:
        item.provider_tested = text_ok
        item.tool_calling_tested = tool_ok
        item.streaming_tested = streaming_ok
        if not (text_ok and tool_ok and streaming_ok):
            item.enabled = False
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> UserAISettingsResponse:
    return await _user_response(db, current_user, await _get_user_settings(db, current_user.id))


@router.put("/api/auth/ai-settings", response_model=UserAISettingsResponse)
async def update_user_ai_settings(
    request: UserAISettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
            admin_prompt=system.admin_prompt or "",
            **_test_model_parameters(request, item),
        )
        text_ok, tool_ok, streaming_ok, message = await test_provider(candidate)
    except (AIConfigurationError, ValueError) as exc:
        text_ok, tool_ok, streaming_ok, message = False, False, False, str(exc)
    saved_configuration = not request.model_fields_set
    if saved_configuration:
        item.provider_tested = text_ok
        item.tool_calling_tested = tool_ok
        item.streaming_tested = streaming_ok
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
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@router.post("/api/ai/conversations", response_model=AIConversationResponse)
async def create_ai_conversation(
    request: AIConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AIConversation:
    if await get_effective_provider(db, current_user) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No tested AI provider is enabled",
        )
    if request.server_id is not None:
        await _server_for_user(db, current_user, request.server_id)
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[AIConversation]:
    result = await db.execute(
        select(AIConversation)
        .where(AIConversation.user_id == current_user.id)
        .order_by(AIConversation.updated_at.desc())
        .limit(100)
    )
    return list(result.scalars().all())


@router.get("/api/ai/conversations/{conversation_id}", response_model=AIConversationDetail)
async def get_ai_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AIConversationDetail:
    conversation = await _conversation_for_user(db, current_user, conversation_id)
    result = await db.execute(
        select(AIMessage)
        .where(
            AIMessage.conversation_id == conversation.id,
            AIMessage.visible.is_(True),
        )
        .order_by(AIMessage.id.asc())
    )
    return AIConversationDetail(
        **AIConversationResponse.model_validate(conversation).model_dump(),
        messages=[AIMessageResponse.model_validate(item) for item in result.scalars().all()],
    )


@router.delete("/api/ai/conversations/{conversation_id}", status_code=204)
async def delete_ai_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> None:
    conversation = await _conversation_for_user(db, current_user, conversation_id)
    await reconcile_waiting_approval_runs(db, conversation_id=conversation.id)
    active_result = await db.execute(
        select(func.count())
        .select_from(AIRun)
        .where(
            AIRun.conversation_id == conversation.id,
            AIRun.status.in_(ACTIVE_RUN_STATUSES),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AIRun:
    conversation = await _conversation_for_user(db, current_user, conversation_id)
    if await get_effective_provider(db, current_user) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No tested AI provider is enabled",
        )
    if conversation.server_id is not None:
        await _server_for_user(db, current_user, conversation.server_id)
    await reconcile_waiting_approval_runs(db, conversation_id=conversation.id)
    active_result = await db.execute(
        select(func.count())
        .select_from(AIRun)
        .where(
            AIRun.conversation_id == conversation.id,
            AIRun.status.in_(ACTIVE_RUN_STATUSES),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    conversation = await _conversation_for_user(db, current_user, conversation_id)
    try:
        result = await interrupt_conversation_run(db, current_user, conversation.id)
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _run_for_user(db: AsyncSession, user: User, run_id: str) -> AIRun:
    result = await db.execute(select(AIRun).where(AIRun.id == run_id, AIRun.user_id == user.id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.get("/api/ai/runs/{run_id}")
async def get_ai_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    run = await _run_for_user(db, current_user, run_id)
    await reconcile_waiting_approval_runs(db, run_id=run.id)
    result = await db.execute(
        select(AIToolRun)
        .where(AIToolRun.run_id == run.id)
        .order_by(AIToolRun.created_at.asc(), AIToolRun.id.asc())
    )
    return {
        **AIRunResponse.model_validate(run).model_dump(mode="json"),
        "tools": [
            AIToolRunResponse.model_validate(item).model_dump(mode="json")
            for item in result.scalars().all()
        ],
    }


@router.get("/api/ai/tasks", response_model=list[AIBackgroundTaskResponse])
async def list_ai_background_tasks(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[AIBackgroundTaskResponse]:
    """Return the caller's active and recently finished AI tasks."""
    await reconcile_waiting_approval_runs(db, user_id=current_user.id)
    run_result = await db.execute(
        select(AIRun)
        .where(
            AIRun.user_id == current_user.id,
            AIRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .order_by(AIRun.updated_at.desc(), AIRun.created_at.desc())
        .limit(limit)
    )
    runs = list(run_result.scalars().all())
    if not runs:
        return []
    run_ids = [run.id for run in runs]
    tool_result = await db.execute(
        select(AIToolRun)
        .where(AIToolRun.run_id.in_(run_ids))
        .order_by(AIToolRun.created_at.asc(), AIToolRun.id.asc())
    )
    tools_by_run: dict[str, list[AIBackgroundTaskToolResponse]] = {}
    for tool in tool_result.scalars().all():
        tools_by_run.setdefault(tool.run_id, []).append(
            AIBackgroundTaskToolResponse(
                id=tool.id,
                tool_name=tool.tool_name,
                risk=tool.risk,
                status=tool.status,
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
    ]


@router.post("/api/ai/runs/{run_id}/tools/{tool_run_id}")
async def decide_ai_tool(
    run_id: str,
    tool_run_id: str,
    request: AIToolDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    item.status = (
        "queued"
        if request.decision == "approve" and item.risk == "write"
        else "approved"
        if request.decision == "approve"
        else "rejected"
    )
    item.approved_by = current_user.id
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


def _encode_sse_event(event: dict[str, Any]) -> str:
    sequence = str(event.get("sequence") or "0")
    event_type = str(event.get("type") or "message").replace("\r", "").replace("\n", "")
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"id: {sequence}\nevent: {event_type}\ndata: {data}\n\n"


@router.get("/api/ai/runs/{run_id}/events/stream")
async def ai_run_event_stream(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StreamingResponse:
    await _run_for_user(db, current_user, run_id)

    async def event_source():
        queue = await ai_event_hub.subscribe_queue(run_id)
        latest_sequence = after
        try:
            yield ": connected\n\n"
            for event in await ai_event_hub.replay(run_id, latest_sequence):
                sequence = int(event.get("sequence") or 0)
                if sequence <= latest_sequence:
                    continue
                latest_sequence = sequence
                yield _encode_sse_event(event)
            idle_ticks = 0
            while not await request.is_disconnected():
                pending_events: list[dict[str, Any]] = []
                try:
                    pending_events.append(await asyncio.wait_for(queue.get(), timeout=1))
                except TimeoutError:
                    idle_ticks += 1
                    if idle_ticks >= 15:
                        idle_ticks = 0
                        yield ": keep-alive\n\n"
                else:
                    idle_ticks = 0
                # A run task and its SSE client may live in different workers.
                # Redis replay turns the local queue into a low-latency wake-up,
                # while still delivering cross-process events within one second.
                pending_events.extend(await ai_event_hub.replay(run_id, latest_sequence))
                pending_events.sort(key=lambda item: int(item.get("sequence") or 0))
                for event in pending_events:
                    sequence = int(event.get("sequence") or 0)
                    if sequence <= latest_sequence:
                        continue
                    latest_sequence = sequence
                    yield _encode_sse_event(event)
                    if event.get("type") in {"run_completed", "run_failed", "run_interrupted"}:
                        return
        finally:
            await ai_event_hub.unsubscribe_queue(run_id, queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/api/ai/runs/{run_id}/events")
async def ai_run_events(
    websocket: WebSocket,
    run_id: str,
    after: int = Query(default=0, ge=0),
) -> None:
    user, _ = await authenticate_websocket(websocket)
    if user is None:
        return
    async with async_session_maker() as db:
        run_result = await db.execute(
            select(AIRun).where(AIRun.id == run_id, AIRun.user_id == user.id)
        )
        run = run_result.scalar_one_or_none()
        if run is None:
            await websocket.close(code=4404, reason="Run not found")
            return
    await websocket.accept()
    await ai_event_hub.subscribe(run_id, websocket)
    try:
        for event in await ai_event_hub.replay(run_id, after):
            await websocket.send_json(event)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ai_event_hub.unsubscribe(run_id, websocket)
