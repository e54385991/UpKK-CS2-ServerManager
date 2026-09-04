"""覆盖 AI 工具的输入契约、权限边界和只读远端工具。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.schemas.discord import AgentCapability
from services import ai_tools


class _Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.scalar


class _Db:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.commits = 0

    async def execute(self, _statement):
        return _Result(self.rows)

    async def get(self, *_args):
        return SimpleNamespace(id=7, is_active=True)

    async def commit(self):
        self.commits += 1


def _server(**overrides):
    values = dict(
        id=3,
        user_id=7,
        name="Test server",
        host="host",
        ssh_port=22,
        game_port=27015,
        game_directory="/srv/cs2",
        session_manager="tmux",
        ssh_health_status="healthy",
        last_ssh_success=None,
        a2s_query_host=None,
        a2s_query_port=None,
        status=SimpleNamespace(value="running"),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _ctx(server=None, db=None):
    return ai_tools.ToolContext(
        db=db or _Db(),
        user=SimpleNamespace(id=7, is_admin=True, is_active=True),
        server=server,
        emit=AsyncMock(),
    )


def test_ai_input_validators_and_tool_specs():
    with pytest.raises(ValueError):
        ai_tools.GameConsoleCommandInput(command="echo one\necho two")
    with pytest.raises(ValueError):
        ai_tools.FilePatchInput(relative_path="x.cfg", expected_revision="new", content="x")
    with pytest.raises(ValueError):
        ai_tools.ServerStartupPlanInput()
    assert ai_tools.ServerStartupPlanInput(default_map="de_dust2").default_map == "de_dust2"
    assert ai_tools._safe_relative_path("cfg\\server.cfg") == "cfg/server.cfg"
    for path in ("", "/etc/passwd", "../secret", "a/../../b", "a\x00b"):
        with pytest.raises(ValueError):
            ai_tools._safe_relative_path(path)
    assert ai_tools._css_log_root(_server()).endswith("counterstrikesharp/logs")
    assert ai_tools._safe_css_log_name("error.LOG") == "error.LOG"
    for name in ("../x.log", ".hidden.log", "error.dll"):
        with pytest.raises(ValueError):
            ai_tools._safe_css_log_name(name)
    spec = ai_tools.ToolSpec(
        "x",
        "desc",
        "read",
        ai_tools.EmptyInput,
        AsyncMock(),
        capability_options=(frozenset({AgentCapability.INSPECT_STATUS}),),
    )
    assert spec.is_exposed(frozenset({AgentCapability.INSPECT_STATUS}))
    assert not spec.is_exposed(frozenset())
    assert spec.api_definition()["function"]["name"] == "x"
    assert ai_tools._control_capabilities({"action": "start"}) == frozenset({AgentCapability.START})
    assert ai_tools._server_operation_capabilities({"operation": "unknown"}) == frozenset()
    assert len(ai_tools.tool_definitions(server_selected=False)) < len(
        ai_tools.tool_definitions(server_selected=True)
    )
    serialized, digest = ai_tools.canonical_arguments({"b": 1, "a": 2})
    assert '"a":2' in serialized and digest == hashlib.sha256(serialized.encode()).hexdigest()


@pytest.mark.asyncio
async def test_ai_read_tools_and_market(monkeypatch):
    server = _server()
    ctx = _ctx(server, _Db())
    monkeypatch.setattr(ai_tools, "authorized_server", AsyncMock(return_value=server))
    assert await ai_tools._require_current_server(ctx) is server
    assert await ai_tools._require_active_user(ctx)
    with pytest.raises(ValueError):
        await ai_tools._require_current_server(_ctx(None))

    class _SSH:
        async def connect(self, _server):
            return True, ""

        async def disconnect(self):
            return None

        async def validate_path_within_base(self, *_args, **_kwargs):
            return True, ""

        async def execute_command(self, command, **_kwargs):
            if "pgrep" in command:
                return True, "binary=ok\nprocesses=1\ndisk_kb=100\ndisk_used_percent=5%\n", ""
            if "console.log" in command:
                return True, "line\npassword=hidden\n", ""
            return True, "cfg/server.cfg\n", ""

        async def read_file(self, *_args, **_kwargs):
            return True, "key=value\n", ""

    monkeypatch.setattr(ai_tools, "SSHManager", _SSH)
    monkeypatch.setattr(ai_tools, "_connect", AsyncMock(return_value=_SSH()))
    from services import a2s_query

    monkeypatch.setattr(
        a2s_query,
        "a2s_service",
        SimpleNamespace(query_server_info=AsyncMock(return_value=(True, {"players": 1}))),
    )
    inspected = await ai_tools.inspect_server(ctx, ai_tools.EmptyInput())
    assert inspected["a2s"]["reachable"]
    found = await ai_tools.search_server_files(ctx, ai_tools.FileSearchInput(query="cfg"))
    assert found["matches"] == ["cfg/server.cfg"]
    read = await ai_tools.read_server_text_file(
        ctx, ai_tools.FileReadInput(relative_path="cfg/server.cfg")
    )
    assert read["content"] and len(read["revision"]) == 64
    tail = await ai_tools.tail_server_log(ctx, ai_tools.TailLogInput(lines=20))
    assert "content" in tail

    plugin = SimpleNamespace(
        id=1,
        title="Plugin",
        version="1",
        category=SimpleNamespace(value="UTILITY"),
        description="desc",
        dependencies=None,
    )
    monkeypatch.setattr(
        ai_tools.MarketPlugin, "search_plugins", AsyncMock(return_value=([plugin], 1))
    )
    result = await ai_tools.search_plugin_market(ctx, ai_tools.PluginSearchInput(query="plugin"))
    assert result["total"] == 1
    with pytest.raises(ValueError):
        await ai_tools.search_plugin_market(ctx, ai_tools.PluginSearchInput(category="bad"))


@pytest.mark.asyncio
async def test_ai_tool_dispatch_and_mutation_wrappers(monkeypatch):
    server = _server()
    ctx = _ctx(server)
    monkeypatch.setattr(ai_tools, "authorized_server", AsyncMock(return_value=server))
    from services import agent_policy_service, change_map_service, custom_command_service

    monkeypatch.setattr(agent_policy_service, "require_agent_capabilities", AsyncMock())
    monkeypatch.setattr(ai_tools, "lookup_knowledge", lambda topic: f"knowledge:{topic}")
    knowledge = await ai_tools.execute_tool(
        "lookup_cs2_knowledge", {"topic": "startup"}, _ctx(None)
    )
    assert knowledge["content"] == "knowledge:startup"
    with pytest.raises(ValueError, match="Unknown tool"):
        await ai_tools.execute_tool("missing", {}, ctx)
    with pytest.raises(ValueError, match="Select a server"):
        await ai_tools.execute_tool("inspect_server", {}, _ctx(None))

    monkeypatch.setattr(
        custom_command_service,
        "execute_custom_commands",
        AsyncMock(return_value={"success": True, "message": "ok"}),
    )
    monkeypatch.setattr(ai_tools, "apply_user_lifecycle_intent", lambda *_args: None)
    manager = SimpleNamespace(
        stop_server=AsyncMock(return_value=(True, "stopped")),
        start_server=AsyncMock(return_value=(True, "started")),
    )
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    for action in ("stop", "start", "restart"):
        result = await ai_tools.control_server(ctx, ai_tools.ServerControlInput(action=action))
        assert result["success"]
    result = await ai_tools.send_game_console_command(
        ctx, ai_tools.GameConsoleCommandInput(command="status")
    )
    assert result["success"]

    monkeypatch.setattr(change_map_service, "load_map_matches", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        change_map_service,
        "workshop_id_fallback",
        lambda query: (
            SimpleNamespace(to_public_dict=lambda: {"id": query}) if query.isdigit() else None
        ),
    )
    pool = await ai_tools.search_map_pool(ctx, ai_tools.MapPoolSearchInput(query="123456"))
    assert pool["count"] == 1
