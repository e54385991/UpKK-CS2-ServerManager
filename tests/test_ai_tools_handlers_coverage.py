"""Exercise AI tool handlers with deterministic database and SSH doubles."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import ai_tools


class _ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.commits = 0

    async def execute(self, _query):
        return _ScalarRows(self.rows)

    async def get(self, _model, _item_id):
        return SimpleNamespace(id=1, is_active=True)

    async def commit(self):
        self.commits += 1


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _SSH:
    def __init__(self, *, execute=None):
        self.execute_calls: list[str] = []
        self.execute_result = execute
        self.disconnected = False

    async def connect(self, _server):
        return True, "connected"

    async def disconnect(self):
        self.disconnected = True

    async def validate_path_within_base(self, *_args, **_kwargs):
        return True, ""

    async def read_file(self, *_args, **_kwargs):
        return True, "safe=1\npassword=***", ""

    async def write_file(self, *_args, **_kwargs):
        return True, ""

    async def execute_command(self, command, **_kwargs):
        self.execute_calls.append(command)
        if self.execute_result is not None:
            return self.execute_result(command)
        if "find " in command and "printf" in command:
            return True, "2.0\tserver.log\t300000\ninvalid", ""
        if "tail" in command and "grep" not in command:
            return True, "ERROR: plugin failed\n", ""
        if "processes=" in command:
            return True, "processes=1\nport_listening=yes\n", ""
        return True, "/srv/cs2/cfg/demo.cfg\n", ""


def _server(**overrides):
    values = dict(
        id=4,
        user_id=1,
        name="demo",
        host="example.test",
        ssh_port=22,
        game_port=27015,
        game_directory="/srv/cs2",
        status=SimpleNamespace(value="running"),
        session_manager="tmux",
        ssh_health_status="healthy",
        last_ssh_success=None,
        a2s_query_host=None,
        a2s_query_port=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _ctx(server=None, *, db=None, user=None, emit=None):
    return ai_tools.ToolContext(
        db=db or _Db(),
        user=user or SimpleNamespace(id=1, is_admin=False, is_active=True),
        server=server,
        emit=emit or AsyncMock(),
    )


def _patch_common(monkeypatch, ssh):
    monkeypatch.setattr(ai_tools, "authorized_server", AsyncMock(return_value=_server()))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: ssh)
    monkeypatch.setattr(
        ai_tools, "maintenance_lock_service", SimpleNamespace(get=lambda *_a, **_k: _Lock())
    )
    monkeypatch.setattr(ai_tools, "enforce_agent_rate_limit", AsyncMock())


@pytest.mark.asyncio
async def test_ai_read_handlers_cover_server_inventory_files_and_logs(monkeypatch):
    server = _server()
    ssh = _SSH()
    _patch_common(monkeypatch, ssh)
    db = _Db()
    ctx = _ctx(server, db=db)

    monkeypatch.setattr(
        ai_tools.Server,
        "get_all_by_user",
        AsyncMock(return_value=[server, _server(id=5, name="disabled")]),
    )
    monkeypatch.setattr(
        "services.agent_policy_service.get_effective_agent_policy",
        AsyncMock(side_effect=[SimpleNamespace(enabled=True), SimpleNamespace(enabled=False)]),
    )
    listed = await ai_tools.list_servers(ctx, ai_tools.EmptyInput())
    assert [item["id"] for item in listed["servers"]] == [4]

    monkeypatch.setattr(
        "services.a2s_query.a2s_service.query_server_info",
        AsyncMock(return_value=(True, {"name": "CS2"})),
    )
    monkeypatch.setattr(
        "services.custom_command_service.read_game_console",
        AsyncMock(return_value={"success": True, "content": "status"}),
    )
    inspected = await ai_tools.inspect_server(ctx, ai_tools.EmptyInput())
    assert "processes" in inspected["inspection"]
    assert inspected["a2s"]["reachable"] is True

    emitted = AsyncMock()
    ctx.emit = emitted
    found = await ai_tools.search_server_files(
        ctx,
        ai_tools.FileSearchInput(
            query="cfg", relative_path="addons", search_content=False, limit=5
        ),
    )
    assert found["count"] >= 1
    emitted.assert_awaited()
    content = await ai_tools.read_server_text_file(
        ctx, ai_tools.FileReadInput(relative_path="cfg/a.cfg")
    )
    assert content["path"] == "cfg/a.cfg"
    assert content["revision"] == hashlib.sha256("safe=1\npassword=***".encode()).hexdigest()
    tail = await ai_tools.tail_server_log(ctx, ai_tools.TailLogInput(lines=20))
    assert tail["path"].endswith("console.log")
    console_read = await ai_tools.read_game_console(ctx, ai_tools.GameConsoleReadInput(lines=20))
    assert isinstance(console_read, dict)
    assert ssh.disconnected


@pytest.mark.asyncio
async def test_ai_css_log_tools_cover_filtering_and_correlation(monkeypatch):
    server = _server()
    ssh = _SSH()
    _patch_common(monkeypatch, ssh)
    monkeypatch.setattr(
        "services.a2s_query.a2s_service.query_server_info",
        AsyncMock(return_value=(False, None)),
    )
    ctx = _ctx(server)
    logs = await ai_tools.list_css_error_logs(
        ctx, ai_tools.CSSLogListInput(keyword="error", limit=5)
    )
    assert logs["untrusted_content"] is True
    assert logs["logs"]

    detail = await ai_tools.read_css_error_log(
        ctx, ai_tools.CSSLogReadInput(log_name="server.log", keyword="error", lines=20)
    )
    assert detail["path"].endswith("server.log")
    assert detail["a2s"]["reachable"] is False
    with pytest.raises(ValueError, match="Select a CounterStrikeSharp"):
        ai_tools._safe_css_log_name("../secret.log")


@pytest.mark.asyncio
async def test_ai_write_handlers_cover_lifecycle_console_and_file_patch(monkeypatch):
    server = _server()
    ssh = _SSH()
    _patch_common(monkeypatch, ssh)
    ctx = _ctx(server)
    manager = SimpleNamespace(
        deploy_cs2_server=AsyncMock(return_value=(True, "deployed")),
        update_server=AsyncMock(return_value=(False, "update failed")),
        validate_server=AsyncMock(return_value=(True, "valid")),
        install_metamod=AsyncMock(return_value=(True, "installed")),
        install_counterstrikesharp=AsyncMock(return_value=(True, "installed")),
        stop_server=AsyncMock(return_value=(True, "stopped")),
        start_server=AsyncMock(return_value=(True, "started")),
    )
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    monkeypatch.setattr(
        "services.plugin_auto_update_service.record_framework_installation", AsyncMock()
    )
    for operation in (
        "deploy",
        "update",
        "validate",
        "install_metamod",
        "install_counterstrikesharp",
    ):
        result = await ai_tools.run_server_operation(
            ctx, ai_tools.ServerOperationInput(operation=operation)
        )
        assert isinstance(result["success"], bool)
    assert ctx.db.commits >= 5

    for action in ("stop", "start", "restart"):
        result = await ai_tools.control_server(ctx, ai_tools.ServerControlInput(action=action))
        assert result["success"] is True

    monkeypatch.setattr(
        "services.custom_command_service.execute_custom_commands",
        AsyncMock(return_value={"success": True, "message": "sent", "token": "redacted"}),
    )
    command = await ai_tools.send_game_console_command(
        ctx, ai_tools.GameConsoleCommandInput(command="status")
    )
    assert command["success"] is True

    current = "old=1"
    expected = hashlib.sha256(current.encode()).hexdigest()
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: ssh)
    ssh.read_file = AsyncMock(return_value=(True, current, ""))
    monkeypatch.setattr("services.audit_log_service.record_audit_event", AsyncMock())
    patched = await ai_tools.patch_server_text_file(
        ctx,
        ai_tools.FilePatchInput(
            relative_path="cfg/server.cfg", expected_revision=expected, content="new=2"
        ),
    )
    assert patched["success"] is True
    assert patched["backup_path"].startswith("cfg/server.cfg.ai-backup-")


@pytest.mark.asyncio
async def test_ai_tool_dispatch_and_approval_summary_cover_validation(monkeypatch):
    server = _server()
    ctx = _ctx(server)
    _patch_common(monkeypatch, _SSH())
    monkeypatch.setattr("services.agent_policy_service.require_agent_capabilities", AsyncMock())
    result = await ai_tools.execute_tool("lookup_cs2_knowledge", {"topic": "layout"}, ctx)
    assert result["topic"] == "layout"
    with pytest.raises(ValueError, match="Unknown tool"):
        await ai_tools.execute_tool("missing", {}, ctx)
    with pytest.raises(ValueError, match="Path must remain"):
        ai_tools._safe_relative_path("../secret")
    with pytest.raises(ValueError, match="recognized text"):
        await ai_tools.patch_server_text_file(
            ctx,
            ai_tools.FilePatchInput(
                relative_path="cfg/a.bin",
                expected_revision="a" * 64,
                content="x",
            ),
        )

    ssh = _SSH()
    _patch_common(monkeypatch, ssh)
    current = "old"
    ssh.read_file = AsyncMock(return_value=(True, current, ""))
    summary = await ai_tools.build_approval_summary(
        "patch_server_text_file",
        {
            "relative_path": "cfg/a.cfg",
            "expected_revision": hashlib.sha256(current.encode()).hexdigest(),
            "content": "new",
        },
        ctx,
    )
    assert summary["target"] == "cfg/a.cfg"
    assert "-old" in summary["diff"] and "+new" in summary["diff"]
