"""Versioned AI assistant workspace for the Next.js console."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from api.dependencies import ActiveUser, DatabaseSession, StreamUser
from api.routes import ai as legacy
from modules.schemas.ai import (
    AIConversationCreate,
    AIConversationDetail,
    AIMessageCreate,
    AIToolDecisionRequest,
)

from .schemas import (
    ActionResult,
    AssistantConversationCreateRequest,
    AssistantConversationDetailView,
    AssistantConversationView,
    AssistantMessageCreateRequest,
    AssistantMessageView,
    AssistantRunDetailView,
    AssistantRunView,
    AssistantToolDecisionRequest,
    AssistantToolView,
    AssistantWorkspaceView,
)

router = APIRouter(prefix="/api/v1/assistant", tags=["v1-assistant"])


def _conversation(item) -> AssistantConversationView:
    return AssistantConversationView(
        id=str(item.id),
        server_id=item.server_id,
        title=item.title,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _message(item) -> AssistantMessageView:
    return AssistantMessageView(
        id=int(item.id),
        role=str(item.role),
        content=item.content,
        tool_name=item.tool_name,
        created_at=item.created_at,
    )


def _run(item) -> AssistantRunView:
    return AssistantRunView(
        id=str(item.id),
        conversation_id=str(item.conversation_id),
        status=str(item.status),
        error=item.error,
    )


@router.get("", response_model=AssistantWorkspaceView)
async def get_assistant_workspace(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AssistantWorkspaceView:
    settings = await legacy.get_user_ai_settings(db, current_user)
    conversations = await legacy.list_ai_conversations(db, current_user)
    return AssistantWorkspaceView(
        provider_ready=bool(settings.effective_enabled),
        mode=settings.effective_source,
        model=settings.model,
        conversations=[_conversation(item) for item in conversations],
    )


@router.post("/conversations", response_model=AssistantConversationView)
async def create_assistant_conversation(
    body: AssistantConversationCreateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AssistantConversationView:
    item = await legacy.create_ai_conversation(
        AIConversationCreate(server_id=body.server_id, title=body.title),
        db,
        current_user,
    )
    return _conversation(item)


@router.get("/conversations/{conversation_id}", response_model=AssistantConversationDetailView)
async def get_assistant_conversation(
    conversation_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AssistantConversationDetailView:
    detail: AIConversationDetail = await legacy.get_ai_conversation(
        conversation_id, db, current_user
    )
    return AssistantConversationDetailView(
        id=detail.id,
        server_id=detail.server_id,
        title=detail.title,
        created_at=detail.created_at,
        updated_at=detail.updated_at,
        messages=[_message(item) for item in detail.messages],
    )


@router.delete("/conversations/{conversation_id}", response_model=ActionResult)
async def delete_assistant_conversation(
    conversation_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    await legacy.delete_ai_conversation(conversation_id, db, current_user)
    return ActionResult(success=True, message="Conversation deleted")


@router.post("/conversations/{conversation_id}/messages", response_model=AssistantRunView)
async def send_assistant_message(
    conversation_id: str,
    body: AssistantMessageCreateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AssistantRunView:
    run = await legacy.send_ai_message(
        conversation_id,
        AIMessageCreate(content=body.content),
        db,
        current_user,
    )
    return _run(run)


@router.post("/conversations/{conversation_id}/interrupt", response_model=ActionResult)
async def interrupt_assistant_conversation(
    conversation_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    payload = await legacy.interrupt_conversation(conversation_id, db, current_user)
    return ActionResult(success=True, message=str(payload.get("message") or "Interrupted"))


@router.get("/runs/{run_id}", response_model=AssistantRunDetailView)
async def get_assistant_run(
    run_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> AssistantRunDetailView:
    payload = await legacy.get_ai_run(run_id, db, current_user)
    tools = [
        AssistantToolView(
            id=str(item["id"]),
            tool_name=str(item.get("tool_name") or ""),
            arguments_hash=str(item.get("arguments_hash") or ""),
            risk=str(item.get("risk") or ""),
            status=str(item.get("status") or ""),
            requires_approval=bool(item.get("requires_approval")),
            error=item.get("error"),
        )
        for item in payload.get("tools") or []
        if isinstance(item, dict)
    ]
    return AssistantRunDetailView(
        id=str(payload["id"]),
        conversation_id=str(payload["conversation_id"]),
        status=str(payload["status"]),
        error=payload.get("error"),
        tools=tools,
    )


@router.get("/runs/{run_id}/events")
async def stream_assistant_run_events(
    run_id: str,
    request: Request,
    db: DatabaseSession,
    current_user: StreamUser,
    after: int = Query(default=0, ge=0),
):
    """Replayable SSE. Cookie or Bearer — EventSource cannot set Authorization."""
    return await legacy.ai_run_event_stream(run_id, request, after, db, current_user)


@router.post("/runs/{run_id}/tools/{tool_run_id}", response_model=ActionResult)
async def decide_assistant_tool(
    run_id: str,
    tool_run_id: str,
    body: AssistantToolDecisionRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    await legacy.decide_ai_tool(
        run_id,
        tool_run_id,
        AIToolDecisionRequest(decision=body.decision, arguments_hash=body.arguments_hash),
        db,
        current_user,
    )
    return ActionResult(success=True, message=body.decision)
