"""AI provider settings, conversations, runs, approvals, and events."""

from __future__ import annotations

# Leaf routes register here in their original position after router initialization.
# ruff: noqa: E402
from typing import Any as Any

from fastapi import APIRouter as APIRouter
from fastapi import HTTPException as HTTPException
from fastapi import Query as Query
from fastapi import status as status
from sqlalchemy import func as func
from sqlalchemy.ext.asyncio import AsyncSession as AsyncSession
from sqlmodel import col as col
from sqlmodel import select as select

from api.dependencies import ActiveUser as ActiveUser
from api.dependencies import AdminUser as AdminUser
from api.dependencies import DatabaseSession as DatabaseSession
from modules import AIConversation as AIConversation
from modules import AIConversationCreate as AIConversationCreate
from modules import AIConversationDetail as AIConversationDetail
from modules import AIConversationResponse as AIConversationResponse
from modules import AIMessage as AIMessage
from modules import AIMessageCreate as AIMessageCreate
from modules import AIMessageResponse as AIMessageResponse
from modules import AIProviderTestRequest as AIProviderTestRequest
from modules import AIProviderTestResponse as AIProviderTestResponse
from modules import AIRun as AIRun
from modules import AIRunResponse as AIRunResponse
from modules import AISystemSettings as AISystemSettings
from modules import AISystemSettingsResponse as AISystemSettingsResponse
from modules import AISystemSettingsUpdate as AISystemSettingsUpdate
from modules import AIToolDecisionRequest as AIToolDecisionRequest
from modules import AIToolRun as AIToolRun
from modules import AIToolRunResponse as AIToolRunResponse
from modules import Server as Server
from modules import User as User
from modules import UserAISettingsResponse as UserAISettingsResponse
from modules import UserAISettingsUpdate as UserAISettingsUpdate
from modules.schemas.ai import AIBackgroundTaskResponse as AIBackgroundTaskResponse
from modules.schemas.ai import AIBackgroundTaskToolResponse as AIBackgroundTaskToolResponse
from modules.utils import get_current_time as get_current_time
from services.agent_policy_service import AgentCapabilityDenied as AgentCapabilityDenied
from services.agent_policy_service import get_effective_agent_policy as get_effective_agent_policy
from services.agent_policy_service import require_agent_capabilities as require_agent_capabilities
from services.ai_access import audit_security_event as audit_security_event
from services.ai_orchestrator import ACTIVE_RUN_STATUSES as ACTIVE_RUN_STATUSES
from services.ai_orchestrator import cleanup_expired_ai_runs as cleanup_expired_ai_runs
from services.ai_orchestrator import interrupt_conversation_run as interrupt_conversation_run
from services.ai_orchestrator import process_ai_run as process_ai_run
from services.ai_orchestrator import (
    reconcile_stale_ai_server_lock as reconcile_stale_ai_server_lock,
)
from services.ai_orchestrator import (
    reconcile_waiting_approval_runs as reconcile_waiting_approval_runs,
)
from services.ai_provider import test_provider as test_provider
from services.ai_security import AIConfigurationError as AIConfigurationError
from services.ai_security import AIProviderConfig as AIProviderConfig
from services.ai_security import credential_encryption_available as credential_encryption_available
from services.ai_security import decrypt_credential as decrypt_credential
from services.ai_security import encrypt_credential as encrypt_credential
from services.ai_security import get_effective_provider as get_effective_provider
from services.ai_security import normalize_base_url as normalize_base_url
from services.task_registry import ai_task_registry as ai_task_registry

from .ai_helpers import _MODEL_PARAMETER_NAMES as _MODEL_PARAMETER_NAMES
from .ai_helpers import _apply_model_parameters as _apply_model_parameters
from .ai_helpers import _apply_saved_provider_test_flags as _apply_saved_provider_test_flags
from .ai_helpers import _apply_system_enabled as _apply_system_enabled
from .ai_helpers import _apply_system_provider_fields as _apply_system_provider_fields
from .ai_helpers import _apply_system_runtime_limits as _apply_system_runtime_limits
from .ai_helpers import _configuration_error as _configuration_error
from .ai_helpers import _get_user_settings as _get_user_settings
from .ai_helpers import _is_saved_provider_test as _is_saved_provider_test
from .ai_helpers import _system_ready_to_enable as _system_ready_to_enable
from .ai_helpers import _system_response as _system_response
from .ai_helpers import _test_model_parameters as _test_model_parameters
from .ai_helpers import _user_response as _user_response

router = APIRouter(tags=["ai-assistant"])


@router.get("/api/system/ai-settings", response_model=AISystemSettingsResponse)
async def get_system_ai_settings(
    db: DatabaseSession,
    current_user: AdminUser,
) -> AISystemSettingsResponse:
    return _system_response(await AISystemSettings.get_or_create(db))


from .ai_settings import (
    _conversation_for_user as _conversation_for_user,
)
from .ai_settings import (
    _server_for_user as _server_for_user,
)
from .ai_settings import (
    get_user_ai_settings as get_user_ai_settings,
)
from .ai_settings import (
    test_system_ai_settings as test_system_ai_settings,
)
from .ai_settings import (
    test_user_ai_settings as test_user_ai_settings,
)
from .ai_settings import update_system_ai_settings as update_system_ai_settings
from .ai_settings import (
    update_user_ai_settings as update_user_ai_settings,
)


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


from . import ai_stream_routes as _ai_stream_routes  # noqa: F401 - compatibility module alias
from .ai_stream_routes import _encode_sse_event as _encode_sse_event
from .ai_stream_routes import (
    ai_run_event_stream as ai_run_event_stream,
)
from .ai_stream_routes import (
    ai_run_events as ai_run_events,
)
