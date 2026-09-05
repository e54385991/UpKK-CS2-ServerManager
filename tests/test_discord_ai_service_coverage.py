"""用本地 fake 覆盖 Discord AI 会话、快照和审批状态机。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.models import AIConversation, AIMessage, AIRun
from services.ai_tools import canonical_arguments

discord_ai = import_module("services.discord_ai_service")


class _Result:
    def __init__(self, *, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar


class _Db:
    def __init__(self, gets=None, results=None):
        self.gets = dict(gets or {})
        self.results = list(results or [])
        self.added = []
        self.commits = 0
        self.flushes = 0

    async def get(self, model, key):
        return self.gets.get((model, key))

    async def execute(self, _query):
        return self.results.pop(0) if self.results else _Result()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


class _Factory:
    def __init__(self, db):
        self.db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return None


def _server(server_id=1, name="CS2 Alpha", user_id=7):
    return SimpleNamespace(id=server_id, name=name, user_id=user_id)


def _owner(user_id=7, active=True):
    return SimpleNamespace(id=user_id, is_active=active)


def _tool(**overrides):
    arguments = {"action": "restart"}
    _serialized, digest = canonical_arguments(arguments)
    values = dict(
        id="tool-1",
        run_id="run-1",
        tool_name="run_server_operation",
        arguments=arguments,
        arguments_hash=digest,
        status="pending_approval",
        approval_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        plan_snapshot={"title": "Restart"},
        progress_snapshot={"step": "queued"},
        approved_by=None,
        approved_at=None,
        approved_actor_type=None,
        approved_external_actor_id=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _run(**overrides):
    values = dict(
        id="run-1",
        conversation_id="conversation-1",
        user_id=7,
        server_id=1,
        source="discord",
        status="queued",
        error=None,
        external_actor_id="actor-1",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_reference_phrases_and_server_resolution_cover_normalization_and_ambiguity():
    phrases = discord_ai._reference_phrases("服务器Ａ１２，CS2 Alpha")
    assert "alpha" in phrases
    assert "cs2" not in phrases
    assert "服务器" not in phrases
    assert discord_ai.resolve_discord_agent_server("anything", []) is None

    only = _server(name="Production")
    assert discord_ai.resolve_discord_agent_server("no explicit name", [only]) is only
    first = _server(name="Alpha 1")
    second = _server(server_id=2, name="Bravo 2")
    assert discord_ai.resolve_discord_agent_server("alpha", [first, second]) is first
    assert discord_ai.resolve_discord_agent_server("alpha bravo", [first, second]) is None
    assert (
        discord_ai.resolve_discord_agent_server("alpha", [SimpleNamespace(id=None, name="Alpha")])
        is None
    )


@pytest.mark.asyncio
async def test_available_server_ids_filter_owner_provider_and_policy(monkeypatch):
    assert (
        await discord_ai.available_discord_agent_server_ids(owner_user_id=1, server_ids=[])
        == frozenset()
    )
    db = _Db({(discord_ai.User, 1): None})
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    assert (
        await discord_ai.available_discord_agent_server_ids(owner_user_id=1, server_ids=[1])
        == frozenset()
    )

    owner = _owner(active=False)
    db = _Db({(discord_ai.User, 1): owner})
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    assert (
        await discord_ai.available_discord_agent_server_ids(owner_user_id=1, server_ids=[1])
        == frozenset()
    )

    owner = _owner()
    db = _Db({(discord_ai.User, 1): owner})
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    monkeypatch.setattr(
        discord_ai,
        "get_effective_provider",
        AsyncMock(side_effect=discord_ai.AIConfigurationError("bad")),
    )
    assert (
        await discord_ai.available_discord_agent_server_ids(owner_user_id=1, server_ids=[1])
        == frozenset()
    )
    monkeypatch.setattr(discord_ai, "get_effective_provider", AsyncMock(return_value=None))
    assert (
        await discord_ai.available_discord_agent_server_ids(owner_user_id=1, server_ids=[1])
        == frozenset()
    )

    db = _Db(
        {(discord_ai.User, 1): owner},
        [_Result(rows=[1, 2, 3])],
    )
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    monkeypatch.setattr(discord_ai, "get_effective_provider", AsyncMock(return_value=object()))
    policies = {
        1: SimpleNamespace(enabled=True),
        2: SimpleNamespace(enabled=False),
        3: SimpleNamespace(enabled=True),
    }
    monkeypatch.setattr(
        discord_ai,
        "get_effective_agent_policy",
        AsyncMock(side_effect=lambda _db, sid: policies[sid]),
    )
    assert await discord_ai.available_discord_agent_server_ids(
        owner_user_id=1, server_ids=[1, 2, 3]
    ) == frozenset({1, 3})


@pytest.mark.asyncio
async def test_reset_and_latest_conversation_use_discord_scope(monkeypatch):
    db = _Db()
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    conversation_id = await discord_ai.reset_discord_conversation(
        owner_user_id=7, server_id=1, actor_user_id="a", guild_id="g", channel_id="c"
    )
    assert conversation_id
    created = db.added[0]
    assert isinstance(created, AIConversation)
    assert created.source == "discord"
    assert db.commits == 1

    latest = SimpleNamespace(id="latest")
    db.results = [_Result(scalar=latest)]
    assert (
        await discord_ai._latest_conversation(
            db, owner_user_id=7, server_id=1, actor_user_id="a", guild_id="g", channel_id="c"
        )
        is latest
    )


@pytest.mark.asyncio
async def test_ask_discord_agent_validates_owner_provider_and_active_run(monkeypatch):
    owner = _owner()
    server = _server()
    db = _Db(
        {(discord_ai.User, 7): owner, (discord_ai.Server, 1): server},
        [_Result(scalar=None), _Result(scalar=0)],
    )
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    monkeypatch.setattr(discord_ai, "require_agent_capabilities", AsyncMock())
    monkeypatch.setattr(discord_ai, "get_effective_provider", AsyncMock(return_value=object()))
    process = AsyncMock()
    monkeypatch.setattr(discord_ai, "process_ai_run", process)
    monkeypatch.setattr(discord_ai, "get_current_time", lambda: datetime.now(timezone.utc))
    run_id = await discord_ai.ask_discord_agent(
        owner_user_id=7,
        server_id=1,
        actor_user_id="actor",
        guild_id="guild",
        channel_id="channel",
        prompt="  restart  ",
    )
    assert run_id
    assert process.await_args.args == (run_id,)
    assert any(isinstance(item, AIMessage) and item.content == "restart" for item in db.added)
    assert any(isinstance(item, AIRun) and item.source == "discord" for item in db.added)
    assert db.flushes == 1

    db = _Db(
        {(discord_ai.User, 7): owner, (discord_ai.Server, 1): server},
        [_Result(scalar=SimpleNamespace(id="existing")), _Result(scalar=1)],
    )
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    with pytest.raises(discord_ai.DiscordAIError, match="active run"):
        await discord_ai.ask_discord_agent(
            owner_user_id=7,
            server_id=1,
            actor_user_id="actor",
            guild_id="guild",
            channel_id="channel",
            prompt="again",
        )

    db = _Db({(discord_ai.User, 7): None, (discord_ai.Server, 1): server})
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    with pytest.raises(discord_ai.DiscordAIError, match="unavailable"):
        await discord_ai.ask_discord_agent(
            owner_user_id=7,
            server_id=1,
            actor_user_id="actor",
            guild_id="guild",
            channel_id="channel",
            prompt="x",
        )

    db = _Db(
        {(discord_ai.User, 7): owner, (discord_ai.Server, 1): SimpleNamespace(id=1, user_id=9)}
    )
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    with pytest.raises(discord_ai.DiscordAIError, match="unavailable"):
        await discord_ai.ask_discord_agent(
            owner_user_id=7,
            server_id=1,
            actor_user_id="actor",
            guild_id="guild",
            channel_id="channel",
            prompt="x",
        )

    db = _Db({(discord_ai.User, 7): owner, (discord_ai.Server, 1): server}, [_Result(scalar=None)])
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    monkeypatch.setattr(discord_ai, "get_effective_provider", AsyncMock(return_value=None))
    with pytest.raises(discord_ai.DiscordAIError, match="No AI provider"):
        await discord_ai.ask_discord_agent(
            owner_user_id=7,
            server_id=1,
            actor_user_id="actor",
            guild_id="guild",
            channel_id="channel",
            prompt="x",
        )


@pytest.mark.asyncio
async def test_discord_run_snapshot_redacts_and_reports_pending_tool(monkeypatch):
    run = _run(status="running", error="err")
    message = SimpleNamespace(content="token=secret@example.com")
    tool = _tool()
    progress = SimpleNamespace(
        tool_name="inspect_server", status="running", progress_snapshot={"n": 1}
    )
    db = _Db(
        {(discord_ai.AIRun, "run-1"): run},
        [_Result(scalar=message), _Result(scalar=tool), _Result(scalar=progress)],
    )
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    snapshot = await discord_ai.discord_run_snapshot("run-1")
    assert snapshot["status"] == "running"
    assert snapshot["message"] != message.content
    assert snapshot["tool"]["id"] == "tool-1"
    assert snapshot["progress"]["tool"] == "inspect_server"

    db = _Db({(discord_ai.AIRun, "missing"): None})
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    with pytest.raises(discord_ai.DiscordAIError, match="not found"):
        await discord_ai.discord_run_snapshot("missing")
    db = _Db({(discord_ai.AIRun, "other"): _run(source="web")})
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    with pytest.raises(discord_ai.DiscordAIError, match="not found"):
        await discord_ai.discord_run_snapshot("other")


@pytest.mark.asyncio
async def test_approve_discord_tool_rejects_stale_or_unauthorized_states(monkeypatch):
    server = _server()
    conversation = SimpleNamespace(
        external_actor_id="actor-1", discord_guild_id="guild", discord_channel_id="channel"
    )
    base_run = _run()
    base_tool = _tool()
    fixed_now = datetime.now(timezone.utc)
    monkeypatch.setattr(discord_ai, "get_current_time", lambda: fixed_now)
    monkeypatch.setattr(discord_ai, "process_ai_run", AsyncMock())
    monkeypatch.setattr(discord_ai, "require_agent_capabilities", AsyncMock())

    async def invoke(db, actor_user_id="actor-1", **kwargs):
        monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
        return await discord_ai.approve_discord_tool(
            run_id="run-1",
            tool_run_id="tool-1",
            actor_user_id=actor_user_id,
            actor_role_ids=set(),
            guild_id="guild",
            channel_id="channel",
            **kwargs,
        )

    for gets, message in [
        ({}, "no longer pending"),
        ({(discord_ai.AIRun, "run-1"): base_run}, "no longer pending"),
        (
            {
                (discord_ai.AIRun, "run-1"): base_run,
                (discord_ai.AIToolRun, "tool-1"): _tool(run_id="other"),
            },
            "no longer pending",
        ),
    ]:
        with pytest.raises(discord_ai.DiscordAIError, match=message):
            await invoke(_Db(gets))

    def db_for(run=base_run, tool=base_tool, conv=conversation):
        return _Db(
            {
                (discord_ai.AIRun, "run-1"): run,
                (discord_ai.AIToolRun, "tool-1"): tool,
                (discord_ai.AIConversation, "conversation-1"): conv,
            }
        )

    with pytest.raises(discord_ai.DiscordAIError, match="original"):
        await invoke(db_for(), actor_user_id="actor-2")
    with pytest.raises(discord_ai.DiscordAIError, match="no expiry"):
        await invoke(db_for(tool=_tool(approval_expires_at=None)))
    with pytest.raises(discord_ai.DiscordAIError, match="expired"):
        await invoke(db_for(tool=_tool(approval_expires_at=fixed_now - timedelta(seconds=1))))
    with pytest.raises(discord_ai.DiscordAIError, match="changed"):
        await invoke(db_for(tool=_tool(arguments={"action": "stop"})))
    with pytest.raises(discord_ai.DiscordAIError, match="selected server"):
        await invoke(db_for(run=_run(server_id=None)))

    monkeypatch.setattr(discord_ai, "authorized_bindings", AsyncMock(return_value=[]))
    with pytest.raises(discord_ai.DiscordAIError, match="revoked"):
        await invoke(db_for())
    monkeypatch.setattr(
        discord_ai, "authorized_bindings", AsyncMock(return_value=[(object(), server)])
    )
    with pytest.raises(discord_ai.DiscordAIError, match="no longer available"):
        await invoke(db_for(tool=_tool(tool_name="missing_tool")))


@pytest.mark.asyncio
async def test_approve_discord_tool_queues_and_processes_valid_request(monkeypatch):
    run = _run()
    tool = _tool()
    conversation = SimpleNamespace(
        external_actor_id="actor-1", discord_guild_id="guild", discord_channel_id="channel"
    )
    db = _Db(
        {
            (discord_ai.AIRun, "run-1"): run,
            (discord_ai.AIToolRun, "tool-1"): tool,
            (discord_ai.AIConversation, "conversation-1"): conversation,
        }
    )
    monkeypatch.setattr(discord_ai, "async_session_maker", _Factory(db))
    monkeypatch.setattr(discord_ai, "get_current_time", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(
        discord_ai, "authorized_bindings", AsyncMock(return_value=[(object(), _server())])
    )
    require = AsyncMock()
    monkeypatch.setattr(discord_ai, "require_agent_capabilities", require)
    process = AsyncMock()
    monkeypatch.setattr(discord_ai, "process_ai_run", process)
    await discord_ai.approve_discord_tool(
        run_id="run-1",
        tool_run_id="tool-1",
        actor_user_id="actor-1",
        actor_role_ids={"role"},
        actor_is_channel_manager=True,
        actor_is_server_administrator=True,
        guild_id="guild",
        channel_id="channel",
    )
    assert tool.status == "queued"
    assert tool.approved_by == 7
    assert tool.approved_actor_type == "discord"
    assert tool.approved_external_actor_id == "actor-1"
    assert db.commits == 1
    process.assert_awaited_once_with("run-1")
    require.assert_awaited_once()
