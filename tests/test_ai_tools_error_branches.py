"""覆盖 AI 工具的错误、权限和敏感文件分支。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import ai_tools
from services.agent_policy_service import AgentCapabilityDenied


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, *, active=True, rows=()):
        self.active = active
        self.rows = list(rows)
        self.commits = 0

    async def execute(self, _query):
        return _Result(self.rows)

    async def get(self, *_args):
        return SimpleNamespace(id=1, is_active=self.active, is_admin=False)

    async def commit(self):
        self.commits += 1


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _SSH:
    def __init__(self, *, command_result=None, valid=(True, ""), read_result=None):
        self.command_result = command_result
        self.valid = valid
        self.read_result = read_result or (True, "safe=1", "")
        self.disconnected = False
        self.commands = []

    async def connect(self, _server):
        return True, "connected"

    async def disconnect(self):
        self.disconnected = True

    async def validate_path_within_base(self, *_args, **_kwargs):
        return self.valid

    async def read_file(self, *_args, **_kwargs):
        return self.read_result

    async def write_file(self, *_args, **_kwargs):
        return True, ""

    async def execute_command(self, command, **_kwargs):
        self.commands.append(command)
        if callable(self.command_result):
            return self.command_result(command)
        if self.command_result is not None:
            return self.command_result
        return True, "", ""


def _server(**overrides):
    values = dict(
        id=4,
        user_id=1,
        name="demo",
        host="example.test",
        ssh_port=22,
        game_port=27015,
        game_directory="/srv/cs2",
        session_manager="tmux",
        status=SimpleNamespace(value="running"),
        ssh_health_status="healthy",
        last_ssh_success=None,
        a2s_query_host=None,
        a2s_query_port=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _ctx(server=None, *, db=None, user=None):
    return ai_tools.ToolContext(
        db=db or _Db(),
        user=user or SimpleNamespace(id=1, is_admin=False, is_active=True),
        server=server or _server(),
        emit=AsyncMock(),
    )


def _patch_lock(monkeypatch):
    monkeypatch.setattr(
        ai_tools.maintenance_lock_service,
        "get",
        lambda *_args, **_kwargs: _Lock(),
    )


@pytest.mark.asyncio
async def test_ai_context_saved_command_and_connection_errors(monkeypatch):
    with pytest.raises(PermissionError, match="no longer active"):
        await ai_tools._require_active_user(_ctx(db=_Db(active=False)))

    monkeypatch.setattr(ai_tools, "SSHManager", lambda: _SSH())
    manager = _SSH()
    manager.connect = AsyncMock(return_value=(False, "offline"))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    with pytest.raises(RuntimeError, match="offline"):
        await ai_tools._connect(_server())

    command = SimpleNamespace(id=3, target="host", commands="echo x", name="x", updated_at=None)
    monkeypatch.setattr(
        ai_tools.CustomCommand,
        "get_by_id_server_and_user",
        AsyncMock(return_value=command),
    )
    _patch_lock(monkeypatch)
    monkeypatch.setattr(ai_tools, "authorized_server", AsyncMock(return_value=_server()))
    ctx = _ctx()
    with pytest.raises(PermissionError, match="changed after approval"):
        await ai_tools.execute_saved_host_command(
            ctx,
            ai_tools.SavedHostCommandInput(command_id=3, expected_command_hash="0" * 64),
        )
    command.target = "game_process"
    with pytest.raises(ValueError, match="unavailable"):
        await ai_tools.execute_saved_host_command(
            ctx,
            ai_tools.SavedHostCommandInput(command_id=3, expected_command_hash="0" * 64),
        )


@pytest.mark.asyncio
async def test_market_preview_handles_panel_native_asset_and_missing_plugin(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=1)
    panel = SimpleNamespace(id=1, title="Panel")
    asset_plugin = SimpleNamespace(id=2, title="Asset")
    monkeypatch.setattr(
        ai_tools.MarketPlugin, "get_by_ids", AsyncMock(return_value=[panel, asset_plugin])
    )
    monkeypatch.setattr(
        "services.plugin_conflict_service._panel_framework_key",
        lambda plugin: "metamod" if plugin is panel else None,
    )
    monkeypatch.setattr(
        "services.plugin_conflict_service._latest_release_asset",
        AsyncMock(
            return_value={"release_id": 9, "release_tag": "v1", "asset_name": "x-steamrt4.zip"}
        ),
    )
    monkeypatch.setattr(
        "services.linux_runtime_service.steam_runtime_for_asset", lambda _name: "steamrt4"
    )
    plan = {"installation_order": [1, 2], "already_installed": []}
    result = await ai_tools._market_release_selection_preview(
        _Db(), server, user, plan, {"reason": "matched"}
    )
    assert result[0]["installation_method"] == "panel_native"
    assert result[1]["steam_runtime"] == "steamrt4"

    monkeypatch.setattr(ai_tools.MarketPlugin, "get_by_ids", AsyncMock(return_value=[]))
    with pytest.raises(ValueError, match="disappeared"):
        await ai_tools._market_release_selection_preview(
            _Db(), server, user, {"installation_order": [3], "already_installed": []}, {}
        )


@pytest.mark.asyncio
async def test_ai_remote_read_handlers_cover_failures_and_empty_logs(monkeypatch):
    server = _server()
    ctx = _ctx(server)
    monkeypatch.setattr(ai_tools, "authorized_server", AsyncMock(return_value=server))
    manager = _SSH(command_result=(False, "", "remote failed"))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    with pytest.raises(RuntimeError, match="remote failed"):
        await ai_tools.inspect_server(ctx, ai_tools.EmptyInput())
    assert manager.disconnected

    manager = _SSH(valid=(False, "outside"))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    with pytest.raises(ValueError, match="outside"):
        await ai_tools.search_server_files(ctx, ai_tools.FileSearchInput(query="cfg"))
    with pytest.raises(ValueError, match="outside"):
        await ai_tools.read_server_text_file(ctx, ai_tools.FileReadInput(relative_path="cfg/a.cfg"))
    with pytest.raises(ValueError, match="outside"):
        await ai_tools.tail_server_log(ctx, ai_tools.TailLogInput(lines=10))

    empty_manager = _SSH(command_result=(True, "", ""))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: empty_manager)
    empty = await ai_tools.tail_server_log(ctx, ai_tools.TailLogInput(lines=10))
    assert "does not exist" in empty["note"]

    failing_manager = _SSH(command_result=(False, "", "search failed"))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: failing_manager)
    with pytest.raises(RuntimeError, match="search failed"):
        await ai_tools.search_server_files(ctx, ai_tools.FileSearchInput(query="cfg"))
    failing_read = _SSH(read_result=(False, "", "read failed"))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: failing_read)
    with pytest.raises(RuntimeError, match="read failed"):
        await ai_tools.read_server_text_file(ctx, ai_tools.FileReadInput(relative_path="cfg/a.cfg"))


@pytest.mark.asyncio
async def test_ai_css_log_filters_invalid_binary_and_keyword_entries(monkeypatch):
    server = _server()
    ctx = _ctx(server)
    monkeypatch.setattr(ai_tools, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(ai_tools, "enforce_agent_rate_limit", AsyncMock())

    def list_commands(command):
        if "find " in command:
            return (
                True,
                "1\tbad.dll\t1\n2\tbad.log\tnope\n3\terror.log\t300000\n4\tother.txt\t4\n",
                "",
            )
        if "grep -Iqi" in command:
            return False, "", ""
        if "test -s" in command:
            return True, "", ""
        return True, "", ""

    manager = _SSH(command_result=list_commands)
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    listed = await ai_tools.list_css_error_logs(
        ctx, ai_tools.CSSLogListInput(keyword="needle", limit=2)
    )
    assert listed["logs"] == []

    binary_manager = _SSH(
        command_result=lambda command: (False, "", "") if "test -s" in command else (True, "", "")
    )
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: binary_manager)
    with pytest.raises(ValueError, match="binary"):
        await ai_tools.read_css_error_log(
            ctx, ai_tools.CSSLogReadInput(log_name="error.log", lines=20)
        )

    invalid_manager = _SSH(valid=(False, "outside"))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: invalid_manager)
    with pytest.raises(ValueError, match="outside"):
        await ai_tools.read_css_error_log(
            ctx, ai_tools.CSSLogReadInput(log_name="error.log", lines=20)
        )


@pytest.mark.asyncio
async def test_ai_patch_file_rejects_null_stale_and_remote_failures(monkeypatch):
    server = _server()
    ctx = _ctx(server)
    monkeypatch.setattr(ai_tools, "authorized_server", AsyncMock(return_value=server))
    current = "old=1"
    revision = hashlib.sha256(current.encode()).hexdigest()
    with pytest.raises(ValueError, match="null bytes"):
        await ai_tools.patch_server_text_file(
            ctx,
            ai_tools.FilePatchInput(
                relative_path="cfg/a.cfg", expected_revision=revision, content="x\x00y"
            ),
        )

    manager = _SSH(read_result=(True, current, ""))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    with pytest.raises(ValueError, match="changed since"):
        await ai_tools.patch_server_text_file(
            ctx,
            ai_tools.FilePatchInput(
                relative_path="cfg/a.cfg", expected_revision="0" * 64, content="new"
            ),
        )

    manager = _SSH(valid=(False, "outside"), read_result=(True, current, ""))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    with pytest.raises(ValueError, match="outside"):
        await ai_tools.patch_server_text_file(
            ctx,
            ai_tools.FilePatchInput(
                relative_path="cfg/a.cfg", expected_revision=revision, content="new"
            ),
        )

    manager = _SSH(read_result=(False, "", "read failed"))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    with pytest.raises(RuntimeError, match="read failed"):
        await ai_tools.patch_server_text_file(
            ctx,
            ai_tools.FilePatchInput(
                relative_path="cfg/a.cfg", expected_revision=revision, content="new"
            ),
        )

    manager = _SSH(command_result=(False, "", "backup failed"), read_result=(True, current, ""))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    with pytest.raises(RuntimeError, match="backup failed"):
        await ai_tools.patch_server_text_file(
            ctx,
            ai_tools.FilePatchInput(
                relative_path="cfg/a.cfg", expected_revision=revision, content="new"
            ),
        )


@pytest.mark.asyncio
async def test_ai_operation_tracking_warning_and_restart_stop_failure(monkeypatch):
    server = _server()
    ctx = _ctx(server, db=_Db())
    monkeypatch.setattr(ai_tools, "authorized_server", AsyncMock(return_value=server))
    _patch_lock(monkeypatch)
    manager = SimpleNamespace(
        update_server=AsyncMock(return_value=(True, "updated")),
        validate_server=AsyncMock(return_value=(False, "invalid")),
        install_metamod=AsyncMock(return_value=(True, "installed")),
        stop_server=AsyncMock(return_value=(False, "still running")),
        start_server=AsyncMock(return_value=(True, "started")),
    )
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: manager)
    monkeypatch.setattr(
        "services.plugin_auto_update_service.record_framework_installation",
        AsyncMock(side_effect=RuntimeError("tracking unavailable")),
    )
    updated = await ai_tools.run_server_operation(
        ctx, ai_tools.ServerOperationInput(operation="update")
    )
    assert updated["success"] is True
    failed = await ai_tools.run_server_operation(
        ctx, ai_tools.ServerOperationInput(operation="validate")
    )
    assert failed["success"] is False
    installed = await ai_tools.run_server_operation(
        ctx, ai_tools.ServerOperationInput(operation="install_metamod")
    )
    assert installed["tracking_warning"]
    restarted = await ai_tools.control_server(ctx, ai_tools.ServerControlInput(action="restart"))
    assert restarted == {"success": False, "message": "Restart stopped before start: still running"}


@pytest.mark.asyncio
async def test_ai_policy_dispatch_and_approval_hash_conflicts(monkeypatch):
    server = _server()
    ctx = _ctx(server)
    monkeypatch.setattr(ai_tools, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(ai_tools, "SSHManager", lambda: _SSH())
    monkeypatch.setattr(
        "services.a2s_query.a2s_service.query_server_info",
        AsyncMock(return_value=(True, {})),
    )
    monkeypatch.setattr("services.agent_policy_service.require_agent_capabilities", AsyncMock())
    await ai_tools.execute_tool("inspect_server", {}, ctx)

    monkeypatch.setattr(
        "services.agent_policy_service.get_effective_agent_policy",
        AsyncMock(return_value=SimpleNamespace(capabilities=set())),
    )
    monkeypatch.setattr(
        ai_tools, "send_game_console_command", AsyncMock(return_value={"success": True})
    )
    with pytest.raises(AgentCapabilityDenied):
        await ai_tools.change_current_map(ctx, ai_tools.ChangeCurrentMapInput(query="dust"))

    plan = {"no_op": False, "value": 1}
    upgrade_service = SimpleNamespace(build_plugin_upgrade_plan=AsyncMock(return_value=plan))
    monkeypatch.setattr(
        "services.plugin_auto_update_service.plugin_auto_update_service", upgrade_service
    )
    with pytest.raises(PermissionError, match="changed after approval"):
        await ai_tools.apply_managed_plugin_upgrade(
            ctx,
            ai_tools.ApplyManagedPluginUpgradeInput(plugin_id=1, expected_plan_hash="0" * 64),
        )

    monkeypatch.setattr(ai_tools, "_require_current_server", AsyncMock(return_value=server))
    for name, arguments, patch_target, message in (
        (
            "apply_workshop_map",
            {"workshop_id_or_url": "1", "expected_plan_hash": "0" * 64},
            "services.workshop_map_service.build_workshop_map_plan",
            "Workshop plan changed",
        ),
        (
            "execute_plugin_crash_isolation",
            {"scope": "both", "expected_plan_hash": "0" * 64},
            "services.plugin_diagnostic_service.build_diagnostic_plan",
            "Diagnostic plan changed",
        ),
        (
            "apply_github_plugin_install",
            {"repo_url": "https://github.com/a/b", "expected_plan_hash": "0" * 64},
            "services.github_plugin_plan_service.build_github_install_plan",
            "GitHub installation plan changed",
        ),
    ):
        mocked = AsyncMock(
            return_value={"plan_hash": "1" * 64, "candidates": [], "health_policy": {}}
        )
        monkeypatch.setattr(patch_target, mocked)
        with pytest.raises(ValueError, match=message):
            await ai_tools.build_approval_summary(name, arguments, ctx)
