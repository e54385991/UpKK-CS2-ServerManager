"""Security and planning coverage for the enhanced CS2 agent tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.models import MarketPlugin
from services.github_plugin_plan_service import (
    GitHubPlanError,
    _github_plan_confirmation_payload,
    _panel_managed_framework,
)
from services.plugin_conflict_service import (
    _panel_framework_key,
    _plugin_plan_confirmation_payload,
    _release_asset_candidates,
)
from services.plugin_inventory_service import installation_evidence
from tests.ai_agent_enhancements_fakes import (
    _startup_server as _startup_server,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupDB as _StartupDB,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupLock as _StartupLock,
)


def test_market_asset_filter_prefers_runtime_linux_archives():
    release = {
        "assets": [
            {
                "name": "plugin-debug-linux.zip",
                "browser_download_url": "https://github.com/a/b/releases/download/v1/debug.zip",
            },
            {
                "name": "plugin-windows.zip",
                "browser_download_url": "https://github.com/a/b/releases/download/v1/win.zip",
            },
            {
                "name": "plugin-upgrade-linux.zip",
                "browser_download_url": "https://github.com/a/b/releases/download/v1/upgrade.zip",
            },
            {
                "name": "plugin-linux.zip",
                "browser_download_url": "https://github.com/a/b/releases/download/v1/linux.zip",
            },
        ]
    }

    candidates = _release_asset_candidates(release)

    assert [item["name"] for item in candidates] == [
        "plugin-linux.zip",
        "plugin-upgrade-linux.zip",
    ]


def test_plugin_approval_hash_payload_ignores_live_inventory_evidence():
    plan = {
        "server_id": 4,
        "plugin": {"id": 7, "title": "Plugin"},
        "dependencies": [{"id": 2, "title": "Dependency"}],
        "installation_order": [2, 7],
        "already_installed": [],
        "tracking_records_without_remote_evidence": ["Plugin"],
        "compatibility_unknown": ["Unknown"],
        "hard_conflicts": [],
        "warnings": [],
        "steps": [{"plugin_id": 7, "status": "install"}],
        "blocked": False,
    }
    refreshed = {
        **plan,
        "already_installed": [2],
        "tracking_records_without_remote_evidence": [],
        "compatibility_unknown": [],
        "steps": [{"plugin_id": 7, "status": "already_installed"}],
    }

    assert _plugin_plan_confirmation_payload(plan) == _plugin_plan_confirmation_payload(refreshed)
    github_plan = {"server_id": 4, "repo_url": "https://github.com/a/b", "already_installed": []}
    github_refreshed = {**github_plan, "already_installed": [2]}
    assert _github_plan_confirmation_payload(github_plan) == _github_plan_confirmation_payload(
        github_refreshed
    )


def test_panel_managed_frameworks_are_recognized_before_generic_installation():
    metamod = MarketPlugin(
        id=1,
        title="Metamod:Source",
        github_url="https://github.com/alliedmodders/metamod-source",
    )
    counterstrikesharp = MarketPlugin(
        id=2,
        title="CounterStrikeSharp",
        github_url="https://github.com/roflmuffin/CounterStrikeSharp",
    )

    assert _panel_framework_key(metamod) == "metamod"
    assert _panel_framework_key(counterstrikesharp) == "counterstrikesharp"
    assert _panel_managed_framework("ALLIEDMODDERS", "metamod-source") == "Metamod:Source"
    assert _panel_managed_framework("roflmuffin", "CounterStrikeSharp") == "CounterStrikeSharp"


@pytest.mark.asyncio
async def test_generic_github_plan_rejects_panel_managed_framework(monkeypatch):
    from modules.schemas.plugins import GitHubPluginInstallPlanRequest
    from services import github_plugin_plan_service

    inspect = AsyncMock()
    monkeypatch.setattr(
        github_plugin_plan_service,
        "authorized_server",
        AsyncMock(return_value=SimpleNamespace(id=4)),
    )
    monkeypatch.setattr(github_plugin_plan_service, "inspect_github_plugin", inspect)

    with pytest.raises(GitHubPlanError, match="panel.*run_server_operation"):
        await github_plugin_plan_service.build_github_install_plan(
            SimpleNamespace(),
            SimpleNamespace(),
            4,
            GitHubPluginInstallPlanRequest(
                repo_url="https://github.com/roflmuffin/CounterStrikeSharp"
            ),
        )

    inspect.assert_not_awaited()


def test_remote_plugin_evidence_does_not_trust_tracking_metadata_alone():
    inventory = {
        "frameworks": {"metamod": False, "counterstrikesharp": False},
        "plugins": [
            {
                "key": "counterstrikesharp:mapchooser",
                "kind": "counterstrikesharp",
                "name": "MapChooser",
                "relative_path": ("cs2/game/csgo/addons/counterstrikesharp/plugins/MapChooser"),
            },
            {
                "key": "metamod:cs2kz.vdf",
                "kind": "metamod",
                "name": "cs2kz.vdf",
                "relative_path": "cs2/game/csgo/addons/metamod/cs2kz.vdf",
            },
        ],
        "truncated": False,
    }

    mapchooser = SimpleNamespace(
        display_name="CS2-Upkk-PanelPLG-Mapchooser",
        repo_url="https://github.com/UpKK-Xnet-Cloud/CS2-Upkk-PanelPLG-Mapchooser",
        framework_key=None,
        custom_install_path=None,
    )
    cs2kz = SimpleNamespace(
        display_name="cs2kz-metamod",
        repo_url="https://github.com/KZGlobalTeam/cs2kz-metamod",
        framework_key=None,
        custom_install_path=None,
    )
    stale = SimpleNamespace(
        display_name="MultiAddonManager",
        repo_url="https://github.com/Source2ZE/MultiAddonManager",
        framework_key=None,
        custom_install_path=None,
    )

    assert installation_evidence(mapchooser, inventory)[0]["name"] == "MapChooser"
    assert installation_evidence(cs2kz, inventory)[0]["name"] == "cs2kz.vdf"
    assert installation_evidence(stale, inventory) == []


def test_simple_admin_install_root_is_not_counterstrikesharp_plugin_evidence():
    simple_admin = SimpleNamespace(
        market_plugin_id=7,
        display_name="CS2-SimpleAdmin",
        repo_url="https://github.com/daffyyyy/CS2-SimpleAdmin",
        framework_key=None,
        custom_install_path="addons/counterstrikesharp",
    )
    inventory = {
        "frameworks": {"metamod": True, "counterstrikesharp": True},
        "plugins": [],
        "truncated": False,
    }

    assert installation_evidence(simple_admin, inventory) == []

    inventory["plugins"] = [
        {
            "key": "counterstrikesharp:cs2-simpleadmin",
            "kind": "counterstrikesharp",
            "name": "CS2-SimpleAdmin",
            "relative_path": ("cs2/game/csgo/addons/counterstrikesharp/plugins/CS2-SimpleAdmin"),
        }
    ]
    assert installation_evidence(simple_admin, inventory)[0]["name"] == "CS2-SimpleAdmin"


@pytest.mark.asyncio
async def test_plugin_plan_does_not_skip_stale_tracking_record(monkeypatch):
    from services import plugin_conflict_service

    target = MarketPlugin(
        id=7,
        github_url="https://github.com/example/MapChooser",
        title="MapChooser",
    )
    tracked = SimpleNamespace(
        market_plugin_id=7,
        display_name="MapChooser",
        repo_url=target.github_url,
        framework_key=None,
        custom_install_path=None,
    )

    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class DB:
        def __init__(self):
            self.results = [Result([tracked]), Result([])]

        async def execute(self, _statement):
            return self.results.pop(0)

    monkeypatch.setattr(
        plugin_conflict_service,
        "_resolve_dependency_order",
        AsyncMock(return_value=([], target)),
    )
    monkeypatch.setattr(
        plugin_conflict_service,
        "inspect_remote_plugin_inventory",
        AsyncMock(
            return_value={
                "frameworks": {"metamod": False, "counterstrikesharp": False},
                "plugins": [],
                "truncated": False,
            }
        ),
    )

    plan = await plugin_conflict_service.build_plugin_install_plan(
        DB(), 4, 7, server=SimpleNamespace(id=4)
    )

    assert plan["already_installed"] == []
    assert plan["steps"][0]["status"] == "install"
    assert plan["steps"][0]["reason"] == "tracking_record_without_remote_evidence"


@pytest.mark.asyncio
async def test_plugin_plan_does_not_skip_simple_admin_when_only_framework_exists(monkeypatch):
    from services import plugin_conflict_service

    target = MarketPlugin(
        id=7,
        github_url="https://github.com/daffyyyy/CS2-SimpleAdmin",
        title="CS2-SimpleAdmin",
    )
    tracked = SimpleNamespace(
        market_plugin_id=7,
        display_name="CS2-SimpleAdmin",
        repo_url=target.github_url,
        framework_key=None,
        custom_install_path="addons/counterstrikesharp",
    )

    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class DB:
        def __init__(self):
            self.results = [Result([tracked]), Result([])]

        async def execute(self, _statement):
            return self.results.pop(0)

    monkeypatch.setattr(
        plugin_conflict_service,
        "_resolve_dependency_order",
        AsyncMock(return_value=([], target)),
    )
    monkeypatch.setattr(
        plugin_conflict_service,
        "inspect_remote_plugin_inventory",
        AsyncMock(
            return_value={
                "frameworks": {"metamod": True, "counterstrikesharp": True},
                "plugins": [],
                "truncated": False,
            }
        ),
    )

    plan = await plugin_conflict_service.build_plugin_install_plan(
        DB(), 4, 7, server=SimpleNamespace(id=4)
    )

    assert plan["already_installed"] == []
    assert plan["steps"][0]["status"] == "install"
    assert plan["steps"][0]["reason"] == "tracking_record_without_remote_evidence"


@pytest.mark.asyncio
async def test_market_asset_preflight_skips_invalid_first_archive(monkeypatch):
    from services import github_plugin_plan_service, plugin_conflict_service

    release = {
        "id": 91,
        "tag_name": "v1.0.0",
        "assets": [
            {
                "id": 1,
                "name": "a-plugin-linux.zip",
                "browser_download_url": (
                    "https://github.com/example/plugin/releases/download/v1/a.zip"
                ),
            },
            {
                "id": 2,
                "name": "b-plugin-linux.zip",
                "browser_download_url": (
                    "https://github.com/example/plugin/releases/download/v1/b.zip"
                ),
            },
        ],
    }
    monkeypatch.setattr(
        plugin_conflict_service,
        "get_effective_github_token",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        plugin_conflict_service.http_helper,
        "get",
        AsyncMock(return_value=(True, release, None)),
    )

    async def inspect_layout(asset, _repo_name):
        if asset["name"].startswith("a-"):
            raise GitHubPlanError("archive layout is invalid")
        return {
            "archive_sha256": "a" * 64,
            "source_prefix": "counterstrikesharp",
            "mapping": [
                {
                    "source": "counterstrikesharp",
                    "target": "addons/counterstrikesharp",
                }
            ],
            "mapping_required": False,
        }

    monkeypatch.setattr(
        github_plugin_plan_service,
        "inspect_release_asset_layout",
        inspect_layout,
    )
    plugin = MarketPlugin(
        id=7,
        github_url="https://github.com/example/plugin",
        title="Plugin",
    )

    selected = await plugin_conflict_service._latest_release_asset(
        SimpleNamespace(),
        plugin,
        SimpleNamespace(github_proxy=None),
        SimpleNamespace(),
    )

    assert selected["asset_name"] == "b-plugin-linux.zip"
    assert selected["source_prefix"] == "counterstrikesharp"
    assert selected["custom_install_path"] == "addons/counterstrikesharp"


@pytest.mark.asyncio
async def test_market_install_refetches_release_and_succeeds_on_third_attempt(monkeypatch):
    from modules import GitHubPluginInstallResponse
    from services import plugin_conflict_service

    asset = {
        "download_url": "https://github.com/example/plugin/releases/download/v1/plugin.zip",
        "release_id": "91",
        "release_tag": "v1",
        "asset_name": "plugin.zip",
        "archive_sha256": "a" * 64,
        "source_prefix": None,
        "custom_install_path": None,
        "allowed_roots": ["addons"],
    }
    select_asset = AsyncMock(return_value=asset)
    install = AsyncMock(
        side_effect=[
            GitHubPluginInstallResponse(success=False, message="Failed to download plugin"),
            GitHubPluginInstallResponse(success=False, message="SSH connection failed"),
            GitHubPluginInstallResponse(success=True, message="Installed", installed_files=4),
        ]
    )
    upsert = AsyncMock()
    monkeypatch.setattr(plugin_conflict_service, "_latest_release_asset", select_asset)
    monkeypatch.setattr(plugin_conflict_service, "install_github_plugin", install)
    monkeypatch.setattr(plugin_conflict_service, "upsert_managed_plugin", upsert)
    plugin = MarketPlugin(
        id=7,
        github_url="https://github.com/example/plugin",
        title="Plugin",
        download_count=0,
        install_count=0,
    )
    db = SimpleNamespace(add=lambda _item: None, commit=AsyncMock())

    result = await plugin_conflict_service._install_one(
        db,
        plugin,
        SimpleNamespace(id=4),
        SimpleNamespace(),
    )

    assert result["success"] is True
    assert select_asset.await_count == 3
    assert install.await_count == 3
    assert upsert.await_count == 1
    assert plugin.download_count == 1
    assert plugin.install_count == 1


@pytest.mark.asyncio
async def test_framework_market_entry_uses_panel_native_installer(monkeypatch):
    from services import plugin_conflict_service

    native_install = AsyncMock(
        return_value={
            "success": True,
            "plugin_id": 7,
            "installation_method": "panel_native",
            "restart_required": True,
        }
    )
    github_install = AsyncMock()
    monkeypatch.setattr(plugin_conflict_service, "_install_panel_framework", native_install)
    monkeypatch.setattr(plugin_conflict_service, "install_github_plugin", github_install)
    plugin = MarketPlugin(
        id=7,
        github_url="https://github.com/roflmuffin/CounterStrikeSharp",
        title="CounterStrikeSharp",
    )

    result = await plugin_conflict_service._install_one(
        SimpleNamespace(),
        plugin,
        SimpleNamespace(id=4),
        SimpleNamespace(),
    )

    assert result["installation_method"] == "panel_native"
    assert result["restart_required"] is True
    native_install.assert_awaited_once()
    github_install.assert_not_awaited()


@pytest.mark.asyncio
async def test_plugin_install_retries_twice_and_succeeds_on_third_attempt(monkeypatch):
    from modules import GitHubPluginInstallRequest, GitHubPluginInstallResponse
    from services import plugin_installation

    install = AsyncMock(
        side_effect=[
            GitHubPluginInstallResponse(success=False, message="Failed to download plugin"),
            GitHubPluginInstallResponse(success=False, message="SSH connection failed"),
            GitHubPluginInstallResponse(success=True, message="Installed", installed_files=4),
        ]
    )
    monkeypatch.setattr(plugin_installation, "install_github_plugin", install)
    progress = AsyncMock()
    request = GitHubPluginInstallRequest(
        download_url="https://github.com/example/plugin/releases/download/v1/plugin.zip"
    )

    result = await plugin_installation.install_github_plugin_with_retry(
        4,
        request,
        SimpleNamespace(),
        SimpleNamespace(),
        ai_progress=progress,
    )

    assert result.success is True
    assert result.installed_files == 4
    assert install.await_count == 3
    assert any("attempt 3/3" in call.args[0] for call in progress.await_args_list)


@pytest.mark.asyncio
async def test_plugin_install_retry_exhaustion_preserves_specific_error(monkeypatch):
    from modules import GitHubPluginInstallRequest, GitHubPluginInstallResponse
    from services import plugin_installation

    install = AsyncMock(
        return_value=GitHubPluginInstallResponse(
            success=False,
            message="No addons/ directory found in archive",
        )
    )
    monkeypatch.setattr(plugin_installation, "install_github_plugin", install)
    request = GitHubPluginInstallRequest(
        download_url="https://github.com/example/plugin/releases/download/v1/plugin.zip"
    )

    result = await plugin_installation.install_github_plugin_with_retry(
        4,
        request,
        SimpleNamespace(),
        SimpleNamespace(),
    )

    assert result.success is False
    assert install.await_count == 3
    assert "No addons/ directory found" in result.message
    assert "2 automatic retries" in result.message
