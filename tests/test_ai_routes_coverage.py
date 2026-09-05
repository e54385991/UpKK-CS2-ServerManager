"""行为测试：覆盖 Web AI 路由的鉴权、配置、任务和审批分支。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import ai as routes
from modules import (
    AIConversation,
    AIConversationCreate,
    AIMessage,
    AIMessageCreate,
    AIProviderTestRequest,
    AIRun,
    AISystemSettings,
    AISystemSettingsUpdate,
    AIToolRun,
    UserAISettings,
    UserAISettingsUpdate,
)
from modules.schemas.ai import AIToolDecisionRequest as SchemaDecision
from modules.utils import get_current_time
from services.agent_policy_service import AgentCapabilityDenied
from services.ai_security import AIConfigurationError


class _Result:
    def __init__(self, value=None, rows=None):
        self.value = value
        self.rows = list(rows or [])

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _DB:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []
        self.deleted = []
        self.commits = 0

    async def execute(self, _statement):
        return self.results.pop(0)

    async def get(self, _model, _key):
        return None

    def add(self, item):
        self.added.append(item)

    async def delete(self, item):
        self.deleted.append(item)

    async def commit(self):
        self.commits += 1

    async def refresh(self, item):
        if isinstance(item, AIRun) and item.id is None:
            item.id = "run-created"


def _user(**values):
    defaults = {"id": 8, "is_admin": False}
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _system(**values):
    return AISystemSettings(id=1, **values)


def _user_settings(**values):
    return UserAISettings(user_id=8, **values)


@pytest.mark.asyncio
async def test_system_settings_get_update_and_enable_guard(monkeypatch):
    item = _system()
    monkeypatch.setattr(routes.AISystemSettings, "get_or_create", AsyncMock(return_value=item))
    db = _DB()
    response = await routes.get_system_ai_settings(db, _user(is_admin=True))
    assert response.enabled is False

    updated = await routes.update_system_ai_settings(
        AISystemSettingsUpdate(
            base_url="https://provider.example/v1",
            model=" test-model ",
            api_key="secret",
            admin_prompt="  hello  ",
            request_timeout_seconds=30,
            history_retention_days=3,
            max_provider_rounds=4,
            max_tool_calls_per_round=5,
            context_window_tokens=65_536,
        ),
        db,
        _user(is_admin=True),
    )
    assert updated.model == "test-model"
    assert item.admin_prompt == "hello"
    assert item.provider_tested is False

    monkeypatch.setattr("api.routes.ai_helpers.credential_encryption_available", lambda: False)
    with pytest.raises(HTTPException) as exc:
        await routes.update_system_ai_settings(
            AISystemSettingsUpdate(enabled=True), db, _user(is_admin=True)
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_system_provider_test_success_and_invalid_configuration(monkeypatch):
    item = _system(base_url="https://saved.example/v1", model="saved", api_key_encrypted="enc")
    db = _DB()
    monkeypatch.setattr(routes.AISystemSettings, "get_or_create", AsyncMock(return_value=item))
    monkeypatch.setattr(routes, "decrypt_credential", lambda _: "key")
    monkeypatch.setattr(
        routes,
        "test_provider",
        AsyncMock(return_value=(True, False, True, "tool unavailable")),
    )
    result = await routes.test_system_ai_settings(AIProviderTestRequest(), db, _user(is_admin=True))
    assert result.success is False
    assert item.provider_tested is True
    assert item.tool_calling_tested is False

    monkeypatch.setattr(routes, "decrypt_credential", lambda _: None)
    invalid = await routes.test_system_ai_settings(
        AIProviderTestRequest(), _DB(), _user(is_admin=True)
    )
    assert invalid.success is False
    assert "required" in invalid.message

    monkeypatch.setattr(
        routes,
        "normalize_base_url",
        lambda _url: (_ for _ in ()).throw(ValueError("bad url")),
    )
    invalid = await routes.test_system_ai_settings(
        AIProviderTestRequest(base_url="bad"), _DB(), _user(is_admin=True)
    )
    assert invalid.message == "bad url"


@pytest.mark.asyncio
async def test_user_settings_get_update_test_modes_and_errors(monkeypatch):
    item = _user_settings()
    monkeypatch.setattr(routes, "_get_user_settings", AsyncMock(return_value=item))
    monkeypatch.setattr(routes, "_user_response", AsyncMock(return_value="user-response"))
    monkeypatch.setattr(routes, "encrypt_credential", lambda value: f"enc:{value}")
    monkeypatch.setattr(routes, "normalize_base_url", lambda value: value.rstrip("/"))
    monkeypatch.setattr(routes, "_apply_model_parameters", lambda _request, _item: True)
    db = _DB()
    assert await routes.get_user_ai_settings(db, _user()) == "user-response"
    result = await routes.update_user_ai_settings(
        UserAISettingsUpdate(
            mode="custom",
            base_url="https://custom.example/v1/",
            model=" model ",
            api_protocol="responses",
            api_key="key",
            clear_api_key=False,
        ),
        db,
        _user(),
    )
    assert result == "user-response"
    assert item.mode == "custom"
    assert item.base_url.endswith("v1")
    assert item.api_key_encrypted == "enc:key"

    monkeypatch.setattr(
        routes,
        "_apply_model_parameters",
        lambda _request, _item: (_ for _ in ()).throw(AIConfigurationError("sampling")),
    )
    with pytest.raises(HTTPException) as exc:
        await routes.update_user_ai_settings(UserAISettingsUpdate(), db, _user())
    assert exc.value.status_code == 422

    monkeypatch.setattr(routes, "_apply_model_parameters", lambda _request, _item: False)
    system = _system()
    monkeypatch.setattr(routes.AISystemSettings, "get_or_create", AsyncMock(return_value=system))
    disabled = await routes.test_user_ai_settings(AIProviderTestRequest(), db, _user())
    assert disabled.success is False
    assert "custom" in disabled.message

    item.mode = "custom"
    monkeypatch.setattr(routes, "decrypt_credential", lambda _: "key")
    monkeypatch.setattr(routes, "test_provider", AsyncMock(return_value=(True, True, True, "ok")))
    result = await routes.test_user_ai_settings(AIProviderTestRequest(), db, _user())
    assert result.success is True

    monkeypatch.setattr(routes, "decrypt_credential", lambda _: None)
    result = await routes.test_user_ai_settings(AIProviderTestRequest(), db, _user())
    assert result.success is False


@pytest.mark.asyncio
async def test_server_and_conversation_helpers_cover_admin_owner_and_missing(monkeypatch):
    server = SimpleNamespace(id=4)
    monkeypatch.setattr(routes.Server, "get_by_id", AsyncMock(return_value=server))
    monkeypatch.setattr(routes.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    assert await routes._server_for_user(_DB(), _user(is_admin=True), 4) is server
    assert await routes._server_for_user(_DB(), _user(), 4) is server
    monkeypatch.setattr(routes.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await routes._server_for_user(_DB(), _user(), 4)
    assert exc.value.status_code == 404

    conversation = AIConversation(id="c1", user_id=8, title="hello", source="web")
    db = _DB(_Result(conversation))
    assert await routes._conversation_for_user(db, _user(), "c1") is conversation
    with pytest.raises(HTTPException) as exc:
        await routes._conversation_for_user(_DB(_Result(None)), _user(), "missing")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_conversation_crud_and_message_lifecycle(monkeypatch):
    user = _user()
    conversation = AIConversation(id="c1", user_id=8, title="New conversation", source="web")
    real_conversation_for_user = routes._conversation_for_user
    monkeypatch.setattr(routes, "_conversation_for_user", AsyncMock(return_value=conversation))
    monkeypatch.setattr(routes, "get_effective_provider", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(routes, "_server_for_user", AsyncMock(return_value=SimpleNamespace(id=3)))
    monkeypatch.setattr(
        routes,
        "get_effective_agent_policy",
        AsyncMock(return_value=SimpleNamespace(enabled=True)),
    )
    monkeypatch.setattr(routes, "reconcile_waiting_approval_runs", AsyncMock(return_value=set()))
    monkeypatch.setattr(routes, "reconcile_stale_ai_server_lock", AsyncMock())
    db = _DB()
    created = await routes.create_ai_conversation(
        AIConversationCreate(server_id=3, title="  title "), db, user
    )
    assert created.title == "title"

    monkeypatch.setattr(routes, "get_effective_provider", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await routes.create_ai_conversation(AIConversationCreate(), _DB(), user)
    assert exc.value.status_code == 409
    monkeypatch.setattr(routes, "get_effective_provider", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(
        routes, "get_effective_agent_policy", AsyncMock(return_value=SimpleNamespace(enabled=False))
    )
    with pytest.raises(HTTPException) as exc:
        await routes.create_ai_conversation(AIConversationCreate(server_id=3), _DB(), user)
    assert exc.value.status_code == 403

    listed = await routes.list_ai_conversations(_DB(_Result(rows=[conversation])), user)
    assert listed == [conversation]
    message = AIMessage(id=1, conversation_id="c1", role="user", content="hi", visible=True)
    monkeypatch.setattr(routes, "_conversation_for_user", real_conversation_for_user)
    detail = await routes.get_ai_conversation(
        "c1", _DB(_Result(conversation), _Result(None, [message])), user
    )
    assert detail.messages[0].content == "hi"

    monkeypatch.setattr(routes, "_conversation_for_user", AsyncMock(return_value=conversation))
    await routes.delete_ai_conversation(
        "c1",
        _DB(_Result(0)),
        user,
    )
    with pytest.raises(HTTPException) as exc:
        await routes.delete_ai_conversation("c1", _DB(_Result(1)), user)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_send_interrupt_and_run_lookup_paths(monkeypatch):
    user = _user()
    conversation = AIConversation(id="c1", user_id=8, title="New conversation", server_id=None)
    monkeypatch.setattr(routes, "_conversation_for_user", AsyncMock(return_value=conversation))
    monkeypatch.setattr(routes, "get_effective_provider", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(routes, "reconcile_waiting_approval_runs", AsyncMock(return_value=set()))
    monkeypatch.setattr(routes.ai_task_registry, "create", lambda coroutine: coroutine.close())
    run = await routes.send_ai_message(
        "c1", AIMessageCreate(content="hello"), _DB(_Result(0)), user
    )
    assert run.status == "queued"
    assert conversation.title == "hello"

    monkeypatch.setattr(routes, "get_effective_provider", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await routes.send_ai_message("c1", AIMessageCreate(content="again"), _DB(), user)
    assert exc.value.status_code == 409
    monkeypatch.setattr(routes, "get_effective_provider", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(
        routes, "interrupt_conversation_run", AsyncMock(return_value={"status": "cancelled"})
    )
    assert await routes.interrupt_conversation("c1", _DB(), user) == {"status": "cancelled"}
    monkeypatch.setattr(
        routes, "interrupt_conversation_run", AsyncMock(side_effect=PermissionError("no"))
    )
    with pytest.raises(HTTPException) as exc:
        await routes.interrupt_conversation("c1", _DB(), user)
    assert exc.value.status_code == 403

    run = AIRun(id="r1", conversation_id="c1", user_id=8, status="done", source="web")
    tool = AIToolRun(
        id="t1",
        run_id="r1",
        tool_call_id="call",
        tool_name="read",
        arguments={},
        arguments_hash="a" * 64,
        status="completed",
        risk="read",
        requires_approval=False,
    )
    monkeypatch.setattr(routes, "_run_for_user", AsyncMock(return_value=run))
    monkeypatch.setattr(routes, "reconcile_waiting_approval_runs", AsyncMock(return_value=set()))
    result = await routes.get_ai_run("r1", _DB(_Result(rows=[tool])), user)
    assert result["id"] == "r1"
    assert result["tools"][0]["id"] == "t1"
    monkeypatch.undo()
    with pytest.raises(HTTPException):
        await routes._run_for_user(_DB(_Result(None)), user, "missing")


@pytest.mark.asyncio
async def test_background_tasks_list_delete_and_endpoint(monkeypatch):
    user = _user()
    run = SimpleNamespace(
        id="r1",
        conversation_id="c1",
        server_id=2,
        status="running",
        error=None,
        created_at=None,
        updated_at=None,
        completed_at=None,
    )
    tool = SimpleNamespace(
        id="t1",
        run_id="r1",
        tool_name="write",
        risk="write",
        status="queued",
        plan_snapshot={},
        progress_snapshot={"done": 1},
        progress_updated_at=None,
        error=None,
        created_at=None,
        completed_at=None,
    )
    monkeypatch.setattr(routes, "reconcile_waiting_approval_runs", AsyncMock(return_value=set()))
    monkeypatch.setattr(routes, "cleanup_expired_ai_runs", AsyncMock())
    tasks = await routes._list_ai_background_tasks(
        _DB(_Result(rows=[run]), _Result(rows=[tool])), user, 10, "c1"
    )
    assert tasks[0].tools[0].tool_name == "write"
    assert await routes.list_ai_background_tasks(10, "c1", _DB(_Result(rows=[])), user) == []
    assert (
        await routes._list_ai_background_tasks_endpoint(
            10, "c1", db=_DB(_Result(rows=[])), current_user=user
        )
        == []
    )

    monkeypatch.setattr(
        routes, "_run_for_user", AsyncMock(return_value=SimpleNamespace(id="r1", status="done"))
    )
    db = _DB()
    assert await routes.delete_ai_background_task("r1", db, user) is None
    assert db.deleted
    monkeypatch.setattr(
        routes, "_run_for_user", AsyncMock(return_value=SimpleNamespace(id="r1", status="running"))
    )
    with pytest.raises(HTTPException) as exc:
        await routes.delete_ai_background_task("r1", _DB(), user)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_tool_approval_rejects_mismatch_expired_and_capability_denial(monkeypatch):
    user = _user()
    run = SimpleNamespace(id="r1", server_id=2)
    item = SimpleNamespace(
        id="t1",
        run_id="r1",
        tool_name="apply_plugin_plan",
        risk="write",
        status="pending_approval",
        requires_approval=True,
        arguments_hash="a" * 64,
        arguments={},
        approval_expires_at=get_current_time() + timedelta(minutes=5),
        approved_by=None,
        approved_actor_type=None,
        approved_at=None,
    )
    monkeypatch.setattr(routes, "_run_for_user", AsyncMock(return_value=run))
    monkeypatch.setattr(routes, "reconcile_waiting_approval_runs", AsyncMock(return_value=set()))
    monkeypatch.setattr(routes, "_server_for_user", AsyncMock(return_value=SimpleNamespace(id=2)))
    monkeypatch.setattr(routes, "require_agent_capabilities", AsyncMock())
    monkeypatch.setattr(routes, "audit_security_event", lambda *_a, **_k: None)
    monkeypatch.setattr(routes.ai_task_registry, "create", lambda coroutine: coroutine.close())
    monkeypatch.setattr(routes, "get_current_time", get_current_time)

    rejected = await routes.decide_ai_tool(
        "r1",
        "t1",
        SchemaDecision(decision="reject", arguments_hash="a" * 64),
        _DB(_Result(item), _Result(0)),
        user,
    )
    assert rejected == {"status": "rejected"}

    item.status = "pending_approval"
    with pytest.raises(HTTPException) as exc:
        await routes.decide_ai_tool(
            "r1",
            "t1",
            SchemaDecision(decision="approve", arguments_hash="b" * 64),
            _DB(_Result(item)),
            user,
        )
    assert exc.value.status_code == 409

    item.arguments_hash = "a" * 64
    item.approval_expires_at = get_current_time() - timedelta(minutes=1)
    with pytest.raises(HTTPException) as exc:
        await routes.decide_ai_tool(
            "r1",
            "t1",
            SchemaDecision(decision="approve", arguments_hash="a" * 64),
            _DB(_Result(item)),
            user,
        )
    assert exc.value.status_code == 409

    item.approval_expires_at = get_current_time() + timedelta(minutes=5)
    monkeypatch.setattr(
        routes, "require_agent_capabilities", AsyncMock(side_effect=AgentCapabilityDenied("denied"))
    )
    with pytest.raises(HTTPException) as exc:
        await routes.decide_ai_tool(
            "r1",
            "t1",
            SchemaDecision(decision="approve", arguments_hash="a" * 64),
            _DB(_Result(item)),
            user,
        )
    assert exc.value.status_code == 403
