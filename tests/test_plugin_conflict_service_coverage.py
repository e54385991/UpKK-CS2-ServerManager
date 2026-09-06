"""覆盖市场插件依赖解析、资产选择和安装计划执行的隔离路径。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules import GitHubPluginInstallResponse
from services import plugin_conflict_service as module
from services.plugins import progress as progress_module


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self.rows)

    def scalar_one_or_none(self):
        return self.scalar


class _Db:
    def __init__(self, *, rows=(), rules=()):
        self.rows = list(rows)
        self.rules = list(rules)
        self.calls = 0
        self.added = []
        self.commit = AsyncMock()

    async def execute(self, _query):
        self.calls += 1
        return _Result(self.rows if self.calls == 1 else self.rules)

    async def delete(self, _value):
        return None

    def add(self, value):
        self.added.append(value)


def _plugin(plugin_id=1, *, deps=None, title=None, url=None, framework=None):
    return SimpleNamespace(
        id=plugin_id,
        title=title or f"Plugin {plugin_id}",
        dependencies=deps,
        github_url=url or f"https://github.com/acme/plugin{plugin_id}",
        download_count=0,
        install_count=0,
        custom_install_path=None,
        framework_key=framework,
        framework="counterstrikesharp",
        version="v1",
    )


def _server():
    return SimpleNamespace(id=7, user_id=3, game_directory="/srv/cs2", github_proxy=None)


def test_conflict_helpers_and_progress_signatures():
    assert module._panel_framework_key(_plugin(title="Metamod")) == "metamod"
    assert module._panel_framework_key(_plugin(url="https://github.com/acme/other")) is None
    assert (
        module._panel_framework_key(_plugin(url="https://github.com/alliedmodders/metamod-source"))
        == "metamod"
    )
    assert (
        module._panel_framework_key(_plugin(url="git@github.com:roflmuffin/counterstrikesharp.git"))
        == "counterstrikesharp"
    )
    assert module._panel_framework_key(_plugin(title="CounterStrikeSharp")) == "counterstrikesharp"
    assert module._restart_payload([])["restart_required"] is False
    assert module._restart_payload([{"restart_required": True}])["restart_required"] is True
    payload = module._plugin_plan_confirmation_payload(
        {
            "server_id": 1,
            "plugin": {},
            "dependencies": [],
            "installation_order": [],
            "hard_conflicts": [],
            "warnings": [],
            "blocked": False,
            "extra": 1,
        }
    )
    assert "extra" not in payload
    assert (
        module._asset_from_download_url(
            _plugin(), "https://github.com/a/b/releases/download/v2/a.zip"
        )["release_tag"]
        == "v2"
    )
    custom = _plugin()
    custom.custom_install_path = "addons/x"
    assert module._asset_from_download_url(custom, "https://example/a.zip")["allowed_roots"] == []

    async def two(message, status):
        return message, status

    async def three(message, status, metadata):
        return message, status, metadata

    async def variable(*args):
        return args

    async def call(progress):
        await module._emit_plan_progress(progress, "hello", step_id="p", step_status="running")

    asyncio.run(call(two))
    asyncio.run(call(three))
    asyncio.run(call(variable))
    asyncio.run(call(None))


@pytest.mark.asyncio
async def test_dependency_resolution_cycle_missing_and_build_plan(monkeypatch):
    plugins = {1: _plugin(1, deps="2"), 2: _plugin(2), 3: _plugin(3, deps="1")}
    monkeypatch.setattr(
        module.MarketPlugin, "get_by_id", AsyncMock(side_effect=lambda _db, key: plugins.get(key))
    )
    dependencies, target = await module._resolve_dependency_order(_Db(), 3)
    assert [item.id for item in dependencies] == [2, 1] and target.id == 3
    with pytest.raises(module.PluginPlanError, match="does not exist"):
        await module._resolve_dependency_order(_Db(), 99)
    plugins[4] = _plugin(4, deps="4")
    with pytest.raises(module.PluginPlanError, match="itself"):
        await module._resolve_dependency_order(_Db(), 4)
    plugins[5] = _plugin(5, deps="6")
    plugins[6] = _plugin(6, deps="5")
    with pytest.raises(module.PluginPlanError, match="cycle"):
        await module._resolve_dependency_order(_Db(), 5)

    target = _plugin(3, deps="1")
    managed = [SimpleNamespace(market_plugin_id=2, display_name="Plugin 2")]
    hard = SimpleNamespace(id=8, plugin_a_id=3, plugin_b_id=2, severity="hard", reason=None)
    warning = SimpleNamespace(id=9, plugin_a_id=1, plugin_b_id=3, severity="warning", reason="warn")
    db = _Db(rows=managed, rules=[hard, warning])
    plugins = {1: _plugin(1, deps="2"), 2: _plugin(2), 3: target}
    monkeypatch.setattr(
        module.MarketPlugin, "get_by_id", AsyncMock(side_effect=lambda _db, key: plugins.get(key))
    )
    monkeypatch.setattr(
        module,
        "inspect_remote_plugin_inventory",
        AsyncMock(return_value={"plugins": [], "frameworks": {}, "truncated": False}),
    )
    monkeypatch.setattr(module, "verified_market_plugin_ids", lambda *_args: {2})
    monkeypatch.setattr(module, "installation_evidence", lambda *_args: [])
    plan = await module.build_plugin_install_plan(db, 7, 3, server=_server())
    assert plan["blocked"] and plan["already_installed"] == [2]
    assert plan["plan_hash"] and plan["steps"][-1]["kind"] == "target"
    with pytest.raises(module.PluginPlanError, match="hard conflict"):
        module.validate_plugin_plan_acknowledgements(plan, set())
    plan["hard_conflicts"] = []
    plan["warnings"] = [{"rule_id": 9}]
    with pytest.raises(module.PluginPlanError, match="acknowledgement"):
        module.validate_plugin_plan_acknowledgements(plan, set())
    module.validate_plugin_plan_acknowledgements(plan, {9})


@pytest.mark.asyncio
async def test_release_assets_latest_and_install_one_paths(monkeypatch):
    plugin = _plugin()
    assets = [
        {"name": "demo-windows.zip", "browser_download_url": "w"},
        {
            "name": "demo-linux.zip",
            "browser_download_url": "https://github.com/a/b/releases/download/v1/demo.zip",
            "size": 2,
        },
        {"name": "demo-debug.zip", "browser_download_url": "d"},
    ]
    candidates = module._release_asset_candidates({"assets": assets})
    assert len(candidates) == 1 and candidates[0]["name"] == "demo-linux.zip"
    monkeypatch.setattr(module, "get_effective_github_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        module.http_helper,
        "get",
        AsyncMock(return_value=(True, {"id": 1, "tag_name": "v1", "assets": assets}, None)),
    )
    runtime = __import__("services.linux_runtime_service", fromlist=["select_unique_runtime_asset"])
    monkeypatch.setattr(runtime, "select_unique_runtime_asset", lambda values, _profile: values[0])
    monkeypatch.setattr(runtime, "has_paired_runtime_assets", lambda _values: False)
    monkeypatch.setattr(runtime, "steam_runtime_for_asset", lambda _value: None)
    github = __import__(
        "services.github_plugin_plan_service", fromlist=["inspect_release_asset_layout"]
    )
    monkeypatch.setattr(
        github,
        "inspect_release_asset_layout",
        AsyncMock(
            return_value={
                "mapping": [{"source": "addons", "target": "addons"}],
                "mapping_required": False,
                "archive_sha256": "a" * 64,
                "source_prefix": "addons",
            }
        ),
    )
    latest = await module._latest_release_asset(
        _Db(), plugin, _server(), SimpleNamespace(id=3), {"reason": "x"}
    )
    assert latest["asset_name"] == "demo-linux.zip"
    plugin.github_url = "invalid"
    with pytest.raises(module.PluginPlanError, match="Invalid GitHub"):
        await module._latest_release_asset(_Db(), plugin, _server(), SimpleNamespace(id=3))
    plugin.github_url = "https://github.com/acme/plugin1"
    monkeypatch.setattr(module.http_helper, "get", AsyncMock(return_value=(False, None, "offline")))
    with pytest.raises(module.PluginPlanError, match="Failed"):
        await module._latest_release_asset(_Db(), plugin, _server(), SimpleNamespace(id=3))

    latest_asset = module._latest_release_asset
    monkeypatch.setattr(
        module.http_helper,
        "get",
        AsyncMock(return_value=(True, {"id": 1, "tag_name": "v1", "assets": []}, None)),
    )
    with pytest.raises(module.PluginPlanError, match="No suitable"):
        await latest_asset(_Db(), plugin, _server(), SimpleNamespace(id=3))

    monkeypatch.setattr(
        module,
        "_latest_release_asset",
        AsyncMock(
            return_value={
                "download_url": "https://github.com/a/b/releases/download/v1/p.zip",
                "release_id": "r",
                "release_tag": "v1",
                "asset_name": "p.zip",
                "custom_install_path": None,
                "source_prefix": None,
                "allowed_roots": ["addons"],
                "steam_runtime": None,
                "archive_sha256": None,
            }
        ),
    )
    monkeypatch.setattr(
        module,
        "install_github_plugin",
        AsyncMock(
            return_value=GitHubPluginInstallResponse(success=True, message="ok", installed_files=1)
        ),
    )
    monkeypatch.setattr(module, "upsert_managed_plugin", AsyncMock())
    result = await module._install_one(
        _Db(), plugin, _server(), SimpleNamespace(id=3), exclude_files=["cfg/x"], upgrade_mode=True
    )
    assert result["success"] and plugin.install_count == 1
    monkeypatch.setattr(
        module,
        "install_github_plugin",
        AsyncMock(
            return_value=GitHubPluginInstallResponse(success=False, message="server not found")
        ),
    )
    failed = await module._install_one(_Db(), plugin, _server(), SimpleNamespace(id=3))
    assert not failed["success"]

    runtime_selection = __import__(
        "services.linux_runtime_service", fromlist=["RuntimeSelectionRequired"]
    )
    monkeypatch.setattr(
        module.http_helper,
        "get",
        AsyncMock(
            return_value=(
                True,
                {
                    "id": 1,
                    "tag_name": "v1",
                    "assets": [
                        {
                            "name": "p-linux.zip",
                            "browser_download_url": "https://github.com/a/b/releases/download/v1/p.zip",
                        },
                        {
                            "name": "p-linux2.zip",
                            "browser_download_url": "https://github.com/a/b/releases/download/v1/p2.zip",
                        },
                    ],
                },
                None,
            )
        ),
    )
    monkeypatch.setattr(runtime_selection, "select_unique_runtime_asset", lambda *_args: None)
    monkeypatch.setattr(runtime_selection, "has_paired_runtime_assets", lambda _values: True)
    with pytest.raises(module.PluginPlanError, match="multiple Steam"):
        await latest_asset(_Db(), plugin, _server(), SimpleNamespace(id=3))

    monkeypatch.setattr(
        module,
        "_latest_release_asset",
        AsyncMock(
            return_value={
                "download_url": "https://github.com/a/b/releases/download/v1/p.zip",
                "release_id": "r",
                "release_tag": "v1",
                "asset_name": "p.zip",
                "custom_install_path": None,
                "source_prefix": None,
                "allowed_roots": ["addons"],
                "archive_sha256": None,
                "steam_runtime": None,
            }
        ),
    )
    install = AsyncMock(
        side_effect=[
            GitHubPluginInstallResponse(success=False, message="network timeout"),
            GitHubPluginInstallResponse(success=True, message="ok"),
        ]
    )
    monkeypatch.setattr(module, "install_github_plugin", install)
    retry = await module._install_one(
        _Db(), plugin, _server(), SimpleNamespace(id=3), progress=AsyncMock()
    )
    assert retry["success"] and install.await_count == 2


@pytest.mark.asyncio
async def test_panel_framework_and_execute_plan_paths(monkeypatch):
    plugin = _plugin(title="Metamod", framework="metamod")
    db = _Db()
    server = _server()
    auto = __import__(
        "services.plugin_auto_update_service", fromlist=["record_framework_installation"]
    )
    monkeypatch.setattr(
        auto, "record_framework_installation", AsyncMock(side_effect=[RuntimeError("track"), None])
    )
    ssh_module = __import__("services.ssh_manager", fromlist=["SSHManager"])
    ssh = SimpleNamespace(
        install_metamod=AsyncMock(return_value=(True, "installed")),
        install_counterstrikesharp=AsyncMock(return_value=(False, "failed")),
    )
    monkeypatch.setattr(ssh_module, "SSHManager", lambda: ssh)
    result = await module._install_panel_framework(
        db, plugin, server, SimpleNamespace(id=3), "metamod", None
    )
    assert result["success"] and "tracking_warning" in result
    counter = _plugin(2, title="CounterStrikeSharp")
    result = await module._install_panel_framework(
        db, counter, server, SimpleNamespace(id=3), "counterstrikesharp", None
    )
    assert not result["success"]

    plan = {
        "plan_hash": "h" * 64,
        "installation_order": [1],
        "already_installed": [1],
        "hard_conflicts": [],
        "warnings": [],
        "blocked": False,
        "dependencies": [],
        "plugin": {"id": 1, "title": "Plugin"},
        "server_id": 7,
    }
    target = _plugin(1)
    monkeypatch.setattr(module, "build_plugin_install_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(module.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    monkeypatch.setattr(module.MarketPlugin, "get_by_ids", AsyncMock(return_value=[target]))
    monkeypatch.setattr(module, "_optional_server_lock", lambda *_args, **_kwargs: _Lock())
    monkeypatch.setattr(
        module,
        "_prepare_plugin_execution",
        AsyncMock(return_value=(plan, {1}, {1: target}, {}, None)),
    )
    success = await module.execute_plugin_install_plan(
        db,
        server,
        SimpleNamespace(id=3, is_admin=False),
        1,
        expected_plan_hash="h" * 64,
        acquire_lock=False,
    )
    assert success["success"] and success["completed"][0]["skipped"]

    plan["installation_order"] = [1, 2]
    second = _plugin(2)
    monkeypatch.setattr(
        module,
        "_prepare_plugin_execution",
        AsyncMock(return_value=(plan, set(), {1: target, 2: second}, {}, None)),
    )
    monkeypatch.setattr(
        module,
        "_install_one",
        AsyncMock(return_value={"plugin_id": 1, "success": False, "message": "failed"}),
    )
    failed = await module.execute_plugin_install_plan(
        db,
        server,
        SimpleNamespace(id=3, is_admin=False),
        1,
        expected_plan_hash="h" * 64,
        acquire_lock=False,
    )
    assert not failed["success"] and failed["remaining"] == [2]


@pytest.mark.asyncio
async def test_prepare_execution_and_lock_progress_error_paths(monkeypatch):
    server = _server()
    plugin = _plugin(1)
    plan = {
        "plan_hash": "h" * 64,
        "already_installed": [],
        "installation_order": [1],
        "hard_conflicts": [],
        "warnings": [],
    }
    monkeypatch.setattr(module, "build_plugin_install_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(module.MarketPlugin, "get_by_ids", AsyncMock(return_value=[plugin]))
    runtime = __import__(
        "services.linux_runtime_service", fromlist=["detect_linux_runtime_profile"]
    )
    monkeypatch.setattr(
        runtime, "detect_linux_runtime_profile", AsyncMock(return_value={"reason": "x"})
    )
    monkeypatch.setattr(
        module,
        "_latest_release_asset",
        AsyncMock(
            return_value={"download_url": "https://github.com/a/b/releases/download/v1/p.zip"}
        ),
    )
    prepared = await module._prepare_plugin_execution(
        _Db(),
        server,
        SimpleNamespace(id=3),
        1,
        set(),
        "h" * 64,
        True,
        "https://github.com/a/b/releases/download/v2/p.zip",
    )
    assert prepared[4] == {"reason": "x"} and prepared[3][1]["release_tag"] == "v2"
    with pytest.raises(module.PluginPlanError, match="changed"):
        await module._prepare_plugin_execution(
            _Db(), server, SimpleNamespace(id=3), 1, set(), "x", True, None
        )

    monkeypatch.setattr(
        progress_module.inspect,
        "signature",
        lambda _progress: (_ for _ in ()).throw(TypeError("signature")),
    )
    called = AsyncMock()
    await module._emit_plan_progress(called, "message", step_id="x", step_status="failed")
    called.assert_awaited_once_with("message", "status")
