"""补充 AI 工具注册表、只读查询和计划包装器的隔离测试。"""

from __future__ import annotations

import hashlib
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import ai_tools as module


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: self.rows)


class _Db:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []
        self.commit = AsyncMock()

    async def execute(self, _query):
        return _Result(self.rows)

    async def get(self, _model, _key):
        return SimpleNamespace(id=3, is_active=True)

    def add(self, value):
        self.added.append(value)


def _server():
    return SimpleNamespace(
        id=7,
        user_id=3,
        name="Demo",
        host="host",
        ssh_port=22,
        game_port=27015,
        game_directory="/srv/cs2",
        status=SimpleNamespace(value="running"),
        session_manager="tmux",
        ssh_health_status="healthy",
        last_ssh_success=None,
    )


def _ctx(server=None, db=None):
    return module.ToolContext(
        db=db or _Db(),
        user=SimpleNamespace(id=3, is_admin=True, is_active=True),
        server=server,
        emit=AsyncMock(),
        run_id="ai-run",
    )


def test_ai_models_capabilities_and_path_boundaries():
    assert module.CSSLogListInput(keyword="x").limit == 20
    assert module.GameConsoleReadInput(lines=20).lines == 20
    with pytest.raises(ValueError):
        module.GameConsoleCommandInput(command="\r")
    with pytest.raises(ValueError):
        module.ServerStartupPlanInput(game_type="10")
    with pytest.raises(ValueError):
        module.FilePatchInput(relative_path="x", expected_revision="A" * 64, content="x")
    command = SimpleNamespace(id=4, target="host", commands="echo hi", updated_at=None)
    digest = module._saved_command_hash(command)
    assert (
        digest
        == hashlib.sha256(
            '{"commands":"echo hi","id":4,"target":"host","updated_at":null}'.encode()
        ).hexdigest()
    )
    spec = module.ToolSpec("x", "x", "read", module.EmptyInput, AsyncMock())
    assert spec.required_capabilities({}) == frozenset()
    assert module._server_operation_capabilities({"operation": "deploy"})
    assert module.tool_definitions(server_selected=True, allowed_capabilities=frozenset())


@pytest.mark.asyncio
async def test_saved_commands_server_listing_and_runtime_profile(monkeypatch):
    server = _server()
    command = SimpleNamespace(
        id=4, name="status", target="host", commands="echo status", updated_at=None
    )
    db = _Db([command])
    ctx = _ctx(server, db)
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    listed = await module.list_saved_host_commands(ctx, module.EmptyInput())
    assert listed["commands"][0]["id"] == 4
    custom = importlib.import_module("services.custom_command_service")
    monkeypatch.setattr(
        module.CustomCommand, "get_by_id_server_and_user", AsyncMock(return_value=command)
    )
    monkeypatch.setattr(
        custom,
        "execute_custom_commands",
        AsyncMock(return_value={"success": True, "message": "ok"}),
    )
    monkeypatch.setattr(module.maintenance_lock_service, "get", lambda *_a, **_k: _Lock())
    result = await module.execute_saved_host_command(
        ctx,
        module.SavedHostCommandInput(
            command_id=4, expected_command_hash=module._saved_command_hash(command)
        ),
    )
    assert result["success"]
    command.target = "game_process"
    with pytest.raises(ValueError, match="unavailable"):
        await module.execute_saved_host_command(
            ctx,
            module.SavedHostCommandInput(command_id=4, expected_command_hash=digest_for(command)),
        )
    command.target = "host"
    monkeypatch.setattr(
        module.CustomCommand, "get_by_id_server_and_user", AsyncMock(return_value=None)
    )
    with pytest.raises(ValueError, match="unavailable"):
        await module.execute_saved_host_command(
            ctx,
            module.SavedHostCommandInput(command_id=4, expected_command_hash="a" * 64),
        )

    monkeypatch.setattr(module.Server, "get_all", AsyncMock(return_value=[server]))
    monkeypatch.setattr(module.Server, "get_all_by_user", AsyncMock(return_value=[]))
    policy = importlib.import_module("services.agent_policy_service")
    monkeypatch.setattr(
        policy, "get_effective_agent_policy", AsyncMock(return_value=SimpleNamespace(enabled=True))
    )
    result = await module.list_servers(_ctx(None, db), module.EmptyInput())
    assert result["servers"][0]["name"] == "Demo"
    monkeypatch.setattr(
        policy, "get_effective_agent_policy", AsyncMock(return_value=SimpleNamespace(enabled=False))
    )
    assert await module.list_servers(_ctx(None, db), module.EmptyInput()) == {"servers": []}

    runtime = importlib.import_module("services.linux_runtime_service")
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(
        runtime, "detect_linux_runtime_profile", AsyncMock(return_value={"reason": "ok"})
    )
    assert await module._optional_linux_runtime_profile(_ctx(None, db)) is None
    assert await module._optional_linux_runtime_profile(_ctx(server, db)) == {"reason": "ok"}


def digest_for(command):
    return module._saved_command_hash(command)


@pytest.mark.asyncio
async def test_market_preview_and_installed_inventory_variants(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=3)
    framework = SimpleNamespace(id=1, title="Metamod")
    ordinary = SimpleNamespace(id=2, title="Plugin")
    monkeypatch.setattr(
        module.MarketPlugin, "get_by_ids", AsyncMock(return_value=[framework, ordinary])
    )
    conflict = importlib.import_module("services.plugin_conflict_service")
    monkeypatch.setattr(
        conflict, "_panel_framework_key", lambda plugin: "metamod" if plugin.id == 1 else None
    )
    monkeypatch.setattr(
        conflict,
        "_latest_release_asset",
        AsyncMock(
            return_value={"release_id": "r", "release_tag": "v1", "asset_name": "p-steamrt3.zip"}
        ),
    )
    runtime = importlib.import_module("services.linux_runtime_service")
    monkeypatch.setattr(
        runtime, "steam_runtime_for_asset", lambda name: "steamrt3" if "steamrt3" in name else None
    )
    preview = await module._market_release_selection_preview(
        _Db(),
        server,
        user,
        {"installation_order": [1, 2, 3], "already_installed": [3]},
        {"reason": "detected"},
    )
    assert preview[0]["installation_method"] == "panel_native"
    assert preview[1]["steam_runtime"] == "steamrt3"

    tracked = [
        SimpleNamespace(
            id=2,
            display_name="Plugin",
            source_type="market",
            market_plugin_id=2,
            framework_key=None,
            installed_version="v1",
        )
    ]
    ctx = _ctx(server, _Db(tracked))
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    inventory = importlib.import_module("services.plugin_inventory_service")
    monkeypatch.setattr(
        module,
        "inspect_remote_plugin_inventory",
        AsyncMock(return_value={"frameworks": {}, "plugins": ["Plugin"], "truncated": False}),
    )
    monkeypatch.setattr(
        module, "installation_evidence", lambda plugin, _inv: {"name": plugin.display_name}
    )
    result = await module.list_installed_plugins(ctx, module.EmptyInput())
    assert result["remote_inspection"]["status"] == "success"
    monkeypatch.setattr(
        module,
        "inspect_remote_plugin_inventory",
        AsyncMock(side_effect=inventory.PluginInventoryError("offline")),
    )
    result = await module.list_installed_plugins(ctx, module.EmptyInput())
    assert result["remote_inspection"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_ai_plan_wrappers_and_dispatch(monkeypatch):
    server = _server()
    ctx = _ctx(server)
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    workshop = importlib.import_module("services.workshop_map_service")
    monkeypatch.setattr(workshop, "build_workshop_map_plan", AsyncMock(return_value={"ok": True}))
    assert (
        await module.plan_workshop_map(ctx, module.WorkshopPlanInput(workshop_id_or_url="123"))
    )["ok"]
    startup = importlib.import_module("services.server_startup_service")
    monkeypatch.setattr(startup, "build_server_startup_plan", lambda *_a: {"ok": True})
    assert (
        await module.plan_server_startup_update(
            ctx, module.ServerStartupPlanInput(default_map="de_dust2")
        )
    )["ok"]
    diagnostics = importlib.import_module("services.plugin_diagnostic_service")
    monkeypatch.setattr(diagnostics, "build_diagnostic_plan", AsyncMock(return_value={"ok": True}))
    assert (await module.plan_plugin_crash_isolation(ctx, module.DiagnosticPlanInput()))["ok"]
    monkeypatch.setattr(diagnostics, "get_diagnostic_run", AsyncMock(return_value={"id": "x"}))
    assert (
        await module.get_plugin_crash_isolation(
            ctx, module.DiagnosticRunInput(diagnostic_id="x" * 36)
        )
    )["id"] == "x"
    monkeypatch.setattr(
        module,
        "TOOLS_BY_NAME",
        {
            "read": module.ToolSpec(
                "read",
                "",
                "read",
                module.EmptyInput,
                AsyncMock(return_value={"ok": True}),
                requires_server=False,
            )
        },
    )
    policy = importlib.import_module("services.agent_policy_service")
    monkeypatch.setattr(policy, "require_agent_capabilities", AsyncMock())
    assert (await module.execute_tool("read", {}, _ctx(None)))["ok"]
    with pytest.raises(ValueError, match="Unknown tool"):
        await module.execute_tool("missing", {}, ctx)


@pytest.mark.asyncio
async def test_ai_plugin_github_diagnostic_and_upgrade_wrappers(monkeypatch):
    server = _server()
    ctx = _ctx(server)
    monkeypatch.setattr(module, "_require_current_server", AsyncMock(return_value=server))
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(module, "enforce_agent_rate_limit", AsyncMock())

    conflicts = importlib.import_module("services.plugin_conflict_service")
    ordinary = SimpleNamespace(id=4, title="Demo")
    monkeypatch.setattr(module.MarketPlugin, "get_by_id", AsyncMock(return_value=ordinary))
    monkeypatch.setattr(conflicts, "_panel_framework_key", lambda _plugin: None)
    monkeypatch.setattr(
        conflicts,
        "build_plugin_install_plan",
        AsyncMock(return_value={"installation_order": [4], "already_installed": []}),
    )
    runtime = importlib.import_module("services.linux_runtime_service")
    monkeypatch.setattr(
        runtime, "detect_linux_runtime_profile", AsyncMock(return_value={"reason": "x"})
    )
    monkeypatch.setattr(
        module, "_market_release_selection_preview", AsyncMock(return_value=[{"plugin_id": 4}])
    )
    assert (await module.plan_plugin_install(ctx, module.PluginPlanInput(plugin_id=4)))[
        "release_selections"
    ]
    monkeypatch.setattr(conflicts, "_panel_framework_key", lambda _plugin: "metamod")
    with pytest.raises(ValueError, match="panel-managed"):
        await module.plan_plugin_install(ctx, module.PluginPlanInput(plugin_id=4))

    diagnostics = importlib.import_module("services.plugin_diagnostic_service")
    monkeypatch.setattr(
        diagnostics, "execute_diagnostic_plan", AsyncMock(return_value={"status": "done"})
    )
    assert (
        await module.execute_plugin_crash_isolation(
            ctx, module.DiagnosticExecuteInput(expected_plan_hash="a" * 64)
        )
    )["status"] == "done"
    monkeypatch.setattr(
        diagnostics, "restore_diagnostic_run", AsyncMock(return_value={"status": "restored"})
    )
    assert (
        await module.restore_plugin_quarantine(
            ctx, module.DiagnosticRunInput(diagnostic_id="a" * 36)
        )
    )["status"] == "restored"

    github = importlib.import_module("services.github_plugin_plan_service")
    monkeypatch.setattr(github, "search_github_plugins", AsyncMock(return_value={"candidates": []}))
    monkeypatch.setattr(github, "inspect_github_plugin", AsyncMock(return_value={"repo_url": "x"}))
    assert (await module.search_github_cs2_plugins(ctx, module.GitHubSearchInput(query="demo")))[
        "candidates"
    ] == []
    assert (
        await module.inspect_github_plugin(
            ctx, module.GitHubInspectInput(repo_url="https://github.com/acme/demo")
        )
    )["repo_url"] == "x"
    monkeypatch.setattr(github, "build_github_install_plan", AsyncMock(return_value={"plan": True}))
    assert (
        await module.plan_github_plugin_install(
            ctx, module.GitHubPlanInput(repo_url="https://github.com/acme/demo")
        )
    )["plan"]
    monkeypatch.setattr(
        github, "execute_github_install_plan", AsyncMock(return_value={"success": True})
    )
    assert (
        await module.apply_github_plugin_install(
            ctx,
            module.GitHubApplyInput(
                repo_url="https://github.com/acme/demo", expected_plan_hash="a" * 64
            ),
        )
    )["success"]

    monkeypatch.setattr(
        conflicts, "execute_plugin_install_plan", AsyncMock(return_value={"success": True})
    )
    ctx.enforce_agent_policy = False
    assert (
        await module.apply_plugin_plan(
            ctx, module.ApplyPluginPlanInput(plugin_id=4, expected_plan_hash="a" * 64)
        )
    )["success"]
    startup = importlib.import_module("services.server_startup_service")
    monkeypatch.setattr(
        startup, "execute_server_startup_plan", AsyncMock(return_value={"success": True})
    )
    assert (
        await module.apply_server_startup_update(
            ctx,
            module.ApplyServerStartupPlanInput(default_map="de_dust2", expected_plan_hash="a" * 64),
        )
    )["success"]

    singleton = importlib.import_module(
        "services.plugin_auto_update_service"
    ).plugin_auto_update_service
    upgrade_plan = {"plugin_id": 4, "no_op": True}
    monkeypatch.setattr(
        singleton, "build_plugin_upgrade_plan", AsyncMock(return_value=upgrade_plan)
    )
    no_op_hash = module.canonical_arguments(upgrade_plan)[1]
    assert (
        await module.apply_managed_plugin_upgrade(
            ctx, module.ApplyManagedPluginUpgradeInput(plugin_id=4, expected_plan_hash=no_op_hash)
        )
    )["no_op"]
    upgrade_plan["no_op"] = False
    monkeypatch.setattr(
        singleton, "build_plugin_upgrade_plan", AsyncMock(return_value=upgrade_plan)
    )
    enqueue_module = importlib.import_module("services.operation_enqueue")
    hub = importlib.import_module("services.server_operation_hub")
    monkeypatch.setattr(
        enqueue_module, "enqueue_plugin_auto_update", AsyncMock(return_value={"operation_id": "op"})
    )
    monkeypatch.setattr(
        hub.server_operation_hub,
        "wait_until_terminal",
        AsyncMock(return_value={"success": True, "message": "updated"}),
    )
    expected = module.canonical_arguments(upgrade_plan)[1]
    result = await module.apply_managed_plugin_upgrade(
        ctx, module.ApplyManagedPluginUpgradeInput(plugin_id=4, expected_plan_hash=expected)
    )
    assert result["success"]
