"""Steam Runtime environment detection and paired-asset selection tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.plugin_market import resolve_latest_market_asset
from modules.models import MarketPlugin
from services.linux_runtime_service import (
    RuntimeSelectionRequired,
    annotate_runtime_assets,
    paired_runtime_families,
    parse_linux_runtime_probe,
    select_unique_runtime_asset,
    steam_runtime_asset_family,
    steam_runtime_for_asset,
)


def _probe(libc: str = "", os_release: str = "") -> str:
    return f"{libc}\n__UPKK_OS_RELEASE__\n{os_release}"


@pytest.mark.parametrize(
    ("glibc_version", "expected"),
    (("2.40", "steamrt3"), ("2.41", "steamrt4"), ("2.43", "steamrt4")),
)
def test_glibc_threshold_selects_runtime(glibc_version, expected):
    profile = parse_linux_runtime_probe(
        _probe(f"glibc {glibc_version}", 'ID=ubuntu\nVERSION_ID="24.04"')
    )

    assert profile["glibc_version"] == glibc_version
    assert profile["recommended_steam_runtime"] == expected
    assert profile["detection_source"] == "glibc"


@pytest.mark.parametrize(
    ("distro_id", "version", "expected"),
    (
        ("ubuntu", "24.04", "steamrt3"),
        ("ubuntu", "25.04", "steamrt4"),
        ("debian", "12", "steamrt3"),
        ("debian", "13", "steamrt4"),
    ),
)
def test_os_release_fallback_is_limited_and_versioned(distro_id, version, expected):
    profile = parse_linux_runtime_probe(
        _probe("", f'ID={distro_id}\nVERSION_ID="{version}"\nPRETTY_NAME="Test Linux"')
    )

    assert profile["recommended_steam_runtime"] == expected
    assert profile["detection_source"] == "os_release"
    assert profile["pretty_name"] == "Test Linux"


def test_musl_and_unknown_distro_do_not_guess():
    profile = parse_linux_runtime_probe(
        _probe("musl libc (x86_64)\nVersion 1.2.5", 'ID=alpine\nVERSION_ID="3.21"')
    )

    assert profile["glibc_version"] is None
    assert profile["recommended_steam_runtime"] is None
    assert profile["detection_source"] == "unknown"


def test_runtime_markers_are_case_and_separator_insensitive():
    rt3 = "Plugin-v1-SteamRT3.tar.gz"
    rt4 = "Plugin-v1-steam-rt-4.tar.gz"

    assert steam_runtime_for_asset(rt3) == "steamrt3"
    assert steam_runtime_for_asset(rt4) == "steamrt4"
    assert steam_runtime_asset_family(rt3) == steam_runtime_asset_family(rt4)


def test_runtime_assets_are_grouped_by_package_family():
    assets = [
        {"name": "Plugin-full-steamrt3.tar.gz"},
        {"name": "Plugin-full-steamrt4.tar.gz"},
        {"name": "Plugin-upgrade-steamrt3.tar.gz"},
        {"name": "Plugin-upgrade-steamrt4.tar.gz"},
        {"name": "Plugin-docs.zip"},
    ]

    assert len(paired_runtime_families(assets)) == 2
    assert select_unique_runtime_asset(assets, {"recommended_steam_runtime": "steamrt4"}) is None


def test_annotations_and_unique_selection_preserve_non_runtime_assets():
    assets = [
        {"name": "Plugin-steamrt3.tar.gz", "url": "rt3"},
        {"name": "Plugin-steamrt4.tar.gz", "url": "rt4"},
    ]
    annotated = annotate_runtime_assets(assets, {"recommended_steam_runtime": "steamrt4"})

    assert [item["runtime_compatibility"] for item in annotated] == [
        "alternative",
        "recommended",
    ]
    assert (
        select_unique_runtime_asset(annotated, {"recommended_steam_runtime": "steamrt4"})["url"]
        == "rt4"
    )
    with pytest.raises(RuntimeSelectionRequired):
        select_unique_runtime_asset(assets, {"recommended_steam_runtime": None})
    assert (
        annotate_runtime_assets([{"name": "Plugin-linux.tar.gz"}], None)[0]["runtime_compatibility"]
        == "not_applicable"
    )


@pytest.mark.asyncio
async def test_market_backend_selects_recommended_runtime(monkeypatch):
    release = {
        "id": 154,
        "tag_name": "v1.5.4",
        "assets": [
            {
                "name": "MultiAddonManager-v1.5.4-steamrt3.tar.gz",
                "browser_download_url": "https://github.com/example/releases/rt3",
            },
            {
                "name": "MultiAddonManager-v1.5.4-steamrt4.tar.gz",
                "browser_download_url": "https://github.com/example/releases/rt4",
            },
        ],
    }
    monkeypatch.setattr(
        "api.routes.plugin_market.get_effective_github_token",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.plugin_market.http_helper.get",
        AsyncMock(return_value=(True, release, None)),
    )
    plugin = MarketPlugin(
        id=1,
        title="MultiAddonManager",
        github_url="https://github.com/Source2ZE/MultiAddonManager",
    )

    selected, error = await resolve_latest_market_asset(
        plugin,
        SimpleNamespace(github_proxy=None),
        SimpleNamespace(),
        SimpleNamespace(),
        {"recommended_steam_runtime": "steamrt4"},
    )

    assert error is None
    assert selected["asset_name"].endswith("steamrt4.tar.gz")


@pytest.mark.asyncio
async def test_release_api_annotates_runtime_assets(monkeypatch):
    from api.routes.github_plugins import get_github_releases

    profile = {
        "distro_id": "ubuntu",
        "distro_version": "25.04",
        "pretty_name": "Ubuntu 25.04",
        "glibc_version": "2.41",
        "recommended_steam_runtime": "steamrt4",
        "detection_source": "glibc",
        "reason": "glibc 2.41 selects SteamRT4",
    }
    release = {
        "id": 154,
        "tag_name": "v1.5.4",
        "assets": [
            {
                "name": "Plugin-steamrt3.tar.gz",
                "browser_download_url": "https://github.com/example/rt3",
                "size": 3,
            },
            {
                "name": "Plugin-steamrt4.tar.gz",
                "browser_download_url": "https://github.com/example/rt4",
                "size": 4,
            },
        ],
    }
    monkeypatch.setattr(
        "services.ai_access.authorized_server",
        AsyncMock(return_value=SimpleNamespace(id=7)),
    )
    monkeypatch.setattr(
        "services.linux_runtime_service.detect_linux_runtime_profile",
        AsyncMock(return_value=profile),
    )
    monkeypatch.setattr(
        "api.routes.github_plugins.get_effective_github_token",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.github_plugins.http_helper.get",
        AsyncMock(return_value=(True, [release], None)),
    )

    response = await get_github_releases(
        "https://github.com/example/Plugin",
        server_id=7,
        db=SimpleNamespace(),
        current_user=SimpleNamespace(),
    )

    assert response.linux_runtime_profile.recommended_steam_runtime == "steamrt4"
    assert [asset.runtime_compatibility for asset in response.releases[0].assets] == [
        "alternative",
        "recommended",
    ]


@pytest.mark.asyncio
async def test_market_backend_rejects_unknown_runtime(monkeypatch):
    release = {
        "id": 154,
        "tag_name": "v1.5.4",
        "assets": [
            {
                "name": "Plugin-steamrt3.tar.gz",
                "browser_download_url": "https://github.com/example/releases/rt3",
            },
            {
                "name": "Plugin-steamrt4.tar.gz",
                "browser_download_url": "https://github.com/example/releases/rt4",
            },
        ],
    }
    monkeypatch.setattr(
        "api.routes.plugin_market.get_effective_github_token",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.plugin_market.http_helper.get",
        AsyncMock(return_value=(True, release, None)),
    )

    with pytest.raises(RuntimeSelectionRequired, match="select an asset explicitly"):
        await resolve_latest_market_asset(
            MarketPlugin(
                id=1,
                title="Plugin",
                github_url="https://github.com/example/Plugin",
            ),
            SimpleNamespace(github_proxy=None),
            SimpleNamespace(),
            SimpleNamespace(),
            {"recommended_steam_runtime": None},
        )


@pytest.mark.asyncio
async def test_market_install_returns_409_before_writes_for_unknown_runtime(monkeypatch):
    from fastapi import HTTPException

    from api.routes import plugin_market

    plugin = MarketPlugin(
        id=1,
        title="Plugin",
        github_url="https://github.com/example/Plugin",
    )
    server = SimpleNamespace(id=7, github_proxy=None)

    class Manager:
        async def connect(self, value):
            return True, "connected"

        async def disconnect(self):
            return None

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(plugin_market, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(
        plugin_market,
        "build_plugin_install_plan",
        AsyncMock(
            return_value={
                "dependencies": [],
                "installation_order": [1],
                "already_installed": [],
                "hard_conflicts": [],
                "warnings": [],
            }
        ),
    )
    monkeypatch.setattr("services.SSHManager", Manager)
    monkeypatch.setattr(
        "services.linux_runtime_service.detect_linux_runtime_profile",
        AsyncMock(return_value={"recommended_steam_runtime": None}),
    )
    resolve = AsyncMock(
        side_effect=RuntimeSelectionRequired(
            "SteamRT3 and SteamRT4 assets are available; select an asset explicitly"
        )
    )
    install = AsyncMock()
    monkeypatch.setattr(plugin_market, "resolve_latest_market_asset", resolve)
    monkeypatch.setattr(plugin_market, "install_github_plugin", install)

    with pytest.raises(HTTPException) as caught:
        await plugin_market.install_plugin(
            1,
            server_id=7,
            download_url=None,
            exclude_dirs=[],
            exclude_files=[],
            install_dependencies=False,
            acknowledge_warning_rule_ids=[],
            upgrade_mode=False,
            db=SimpleNamespace(),
            current_user=SimpleNamespace(),
            _operation_server=server,
        )

    assert caught.value.status_code == 409
    install.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_install_preserves_explicit_runtime_override(monkeypatch):
    from api.routes import plugin_market
    from modules.schemas.plugins import GitHubPluginInstallResponse

    plugin = MarketPlugin(
        id=1,
        title="Plugin",
        github_url="https://github.com/example/Plugin",
        version="v1.5.4",
    )
    server = SimpleNamespace(id=7, github_proxy=None)
    download_url = (
        "https://github.com/example/Plugin/releases/download/v1.5.4/Plugin-steamrt3.tar.gz"
    )

    class DB:
        async def commit(self):
            return None

        async def rollback(self):
            return None

        async def refresh(self, value):
            return None

        def add(self, value):
            return None

    class Manager:
        async def connect(self, value):
            return True, "connected"

        async def disconnect(self):
            return None

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(plugin_market, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(
        plugin_market,
        "build_plugin_install_plan",
        AsyncMock(
            return_value={
                "dependencies": [],
                "installation_order": [1],
                "already_installed": [],
                "hard_conflicts": [],
                "warnings": [],
            }
        ),
    )
    monkeypatch.setattr("services.SSHManager", Manager)
    detect = AsyncMock()
    resolve = AsyncMock()
    upsert = AsyncMock()
    monkeypatch.setattr("services.linux_runtime_service.detect_linux_runtime_profile", detect)
    monkeypatch.setattr(plugin_market, "resolve_latest_market_asset", resolve)
    monkeypatch.setattr(
        plugin_market,
        "install_github_plugin",
        AsyncMock(return_value=GitHubPluginInstallResponse(success=True, message="installed")),
    )
    monkeypatch.setattr("services.plugin_auto_update_service.upsert_managed_plugin", upsert)

    result = await plugin_market.install_plugin(
        1,
        server_id=7,
        download_url=download_url,
        exclude_dirs=[],
        exclude_files=[],
        install_dependencies=False,
        acknowledge_warning_rule_ids=[],
        upgrade_mode=False,
        db=DB(),
        current_user=SimpleNamespace(),
        _operation_server=server,
    )

    assert result.success is True
    detect.assert_not_awaited()
    resolve.assert_not_awaited()
    assert upsert.await_args.kwargs["installed_asset_name"] == "Plugin-steamrt3.tar.gz"


@pytest.mark.asyncio
async def test_github_plan_hash_binds_runtime_and_explicit_override_warns(monkeypatch):
    from modules.schemas.plugins import GitHubPluginInstallPlanRequest
    from services import github_plugin_plan_service

    class Result:
        def scalar_one_or_none(self):
            return None

    class DB:
        async def execute(self, statement):
            return Result()

    server = SimpleNamespace(id=4, user_id=9, game_directory="/srv/cs2")
    profile = {
        "recommended_steam_runtime": "steamrt3",
        "detection_source": "glibc",
        "reason": "glibc 2.40 selects SteamRT3",
    }

    async def detect(_server):
        return dict(profile)

    async def inspect(db, user, repo_url, mode, linux_runtime_profile):
        recommended = linux_runtime_profile["recommended_steam_runtime"]
        assets = [
            {
                "name": "Plugin-steamrt3.tar.gz",
                "url": "https://github.com/example/releases/rt3",
                "runtime_compatibility": (
                    "recommended" if recommended == "steamrt3" else "alternative"
                ),
            },
            {
                "name": "Plugin-steamrt4.tar.gz",
                "url": "https://github.com/example/releases/rt4",
                "runtime_compatibility": (
                    "recommended" if recommended == "steamrt4" else "alternative"
                ),
            },
        ]
        return {
            "repo_url": repo_url,
            "release": {"id": "10", "tag": "v1", "assets": assets},
            "selected_asset": next(
                asset for asset in assets if asset["runtime_compatibility"] == "recommended"
            ),
            "documentation": {"readme": "", "release_notes": ""},
            "warnings": ["SteamRT3 and SteamRT4 assets are available; select an asset explicitly"],
        }

    monkeypatch.setattr(
        github_plugin_plan_service, "authorized_server", AsyncMock(return_value=server)
    )
    monkeypatch.setattr("services.linux_runtime_service.detect_linux_runtime_profile", detect)
    monkeypatch.setattr(github_plugin_plan_service, "inspect_github_plugin", inspect)
    monkeypatch.setattr(
        github_plugin_plan_service,
        "inspect_release_asset_layout",
        AsyncMock(
            return_value={
                "archive_sha256": "a" * 64,
                "entries": [],
                "source_prefix": ".",
                "mapping": [{"source": ".", "target": "addons"}],
                "mapping_required": False,
            }
        ),
    )
    request = GitHubPluginInstallPlanRequest(repo_url="https://github.com/example/Plugin")

    rt3_plan = await github_plugin_plan_service.build_github_install_plan(
        DB(), SimpleNamespace(), server.id, request
    )
    profile.update(
        recommended_steam_runtime="steamrt4",
        reason="glibc 2.41 selects SteamRT4",
    )
    rt4_plan = await github_plugin_plan_service.build_github_install_plan(
        DB(), SimpleNamespace(), server.id, request
    )
    override_plan = await github_plugin_plan_service.build_github_install_plan(
        DB(),
        SimpleNamespace(),
        server.id,
        GitHubPluginInstallPlanRequest(
            repo_url=request.repo_url,
            asset_name="Plugin-steamrt3.tar.gz",
        ),
    )

    assert rt3_plan["asset"]["name"].endswith("steamrt3.tar.gz")
    assert rt4_plan["asset"]["name"].endswith("steamrt4.tar.gz")
    assert rt3_plan["plan_hash"] != rt4_plan["plan_hash"]
    assert any("overrides" in warning for warning in override_plan["warnings"])
    assert not any("select an asset explicitly" in warning for warning in override_plan["warnings"])


@pytest.mark.asyncio
async def test_ai_market_execution_resolves_all_runtime_assets_before_writes(monkeypatch):
    from services import plugin_conflict_service
    from services.plugin_conflict_service import PluginPlanError

    first = MarketPlugin(
        id=1,
        title="First",
        github_url="https://github.com/example/First",
    )
    ambiguous = MarketPlugin(
        id=2,
        title="Ambiguous",
        github_url="https://github.com/example/Ambiguous",
    )
    server = SimpleNamespace(id=7)
    user = SimpleNamespace(id=9, is_admin=True)
    plan = {
        "plan_hash": "a" * 64,
        "already_installed": [],
        "installation_order": [1, 2],
        "hard_conflicts": [],
        "warnings": [],
    }
    monkeypatch.setattr(
        plugin_conflict_service,
        "build_plugin_install_plan",
        AsyncMock(return_value=plan),
    )
    monkeypatch.setattr(
        plugin_conflict_service,
        "validate_plugin_plan_acknowledgements",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("modules.models.Server.get_by_id", AsyncMock(return_value=server))
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=[first, ambiguous]))
    monkeypatch.setattr(
        "services.linux_runtime_service.detect_linux_runtime_profile",
        AsyncMock(return_value={"recommended_steam_runtime": None}),
    )
    resolve = AsyncMock(
        side_effect=[
            {"asset_name": "First-linux.tar.gz"},
            PluginPlanError(
                "Ambiguous: SteamRT3 and SteamRT4 assets are available, but detection failed"
            ),
        ]
    )
    install = AsyncMock()
    monkeypatch.setattr(plugin_conflict_service, "_latest_release_asset", resolve)
    monkeypatch.setattr(plugin_conflict_service, "_install_one", install)

    with pytest.raises(PluginPlanError, match="SteamRT3 and SteamRT4"):
        await plugin_conflict_service.execute_plugin_install_plan(
            SimpleNamespace(),
            server,
            user,
            plugin_id=2,
            expected_plan_hash=plan["plan_hash"],
            acquire_lock=False,
        )

    assert resolve.await_count == 2
    install.assert_not_awaited()
