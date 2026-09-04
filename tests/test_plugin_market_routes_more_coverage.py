"""Additional isolated coverage for market CRUD and install wrappers."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.routes import plugin_market
from modules import GitHubRepoInfo, MarketPlugin, PluginCategory, PluginConflictRuleInput


def _plugin(plugin_id=1, **overrides):
    values = {
        "id": plugin_id,
        "github_url": f"https://github.com/acme/plugin-{plugin_id}",
        "title": f"Plugin {plugin_id}",
        "description": "Description",
        "author": "Author",
        "version": "1.0",
        "category": PluginCategory.UTILITY,
        "tags": "tag",
        "dependencies": None,
        "is_recommended": False,
        "icon_url": None,
        "custom_install_path": None,
        "download_count": 0,
        "install_count": 0,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return MarketPlugin(**values)


class _Db:
    def __init__(self, *, rows=None):
        self.rows = rows or []
        self.added = []
        self.add = Mock(side_effect=self.added.append)
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.flush = AsyncMock()
        self.delete = AsyncMock()

    async def execute(self, _statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.rows), scalar_one_or_none=lambda: self.rows[0] if self.rows else None)


def _admin():
    return SimpleNamespace(id=1, is_admin=True, username="admin")


def test_market_parse_helpers_cover_valid_and_invalid_inputs():
    assert plugin_market._requested_release(None) == (None, None, None)
    assert plugin_market._requested_release(
        "https://github.com/acme/repo/releases/download/v1/plugin.zip"
    ) == ("tag:v1", "v1", "plugin.zip")
    assert plugin_market._requested_release("https://github.com/acme/repo/releases/download/v1") == (
        None,
        None,
        None,
    )
    with pytest.raises(HTTPException):
        plugin_market._requested_release("https://example.com/a.zip")
    assert plugin_market.parse_github_url("git@github.com:acme/repo.git") == ("acme", "repo")
    assert plugin_market.parse_github_url("https://github.com/acme/repo/extra") == ("acme", "repo")
    with pytest.raises(ValueError):
        plugin_market.parse_github_url("https://gitlab.com/acme/repo")
    assert plugin_market.parse_dependency_ids(None) == []
    assert plugin_market.parse_dependency_ids("1, 2,,3") == [1, 2, 3]
    with pytest.raises(ValueError):
        plugin_market.parse_dependency_ids("1,x")


@pytest.mark.asyncio
async def test_market_list_get_and_crud_routes_cover_validation(monkeypatch):
    plugin = _plugin()
    db = _Db()
    monkeypatch.setattr(MarketPlugin, "search_plugins", AsyncMock(return_value=([plugin], 1)))
    monkeypatch.setattr(
        plugin_market,
        "populate_dependency_details",
        AsyncMock(return_value=[plugin]),
    )
    result = await plugin_market.list_plugins(
        page=2,
        page_size=10,
        category="utility",
        search="plugin",
        db=db,
        current_user=SimpleNamespace(id=1),
    )
    assert result.page == 2
    assert result.total_pages == 1
    MarketPlugin.search_plugins.assert_awaited_once()
    with pytest.raises(HTTPException) as caught:
        await plugin_market.list_plugins(
            page=1, page_size=10, category="invalid", search=None, db=db, current_user=SimpleNamespace()
        )
    assert caught.value.status_code == 400

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(plugin_market, "populate_dependency_details", AsyncMock(return_value=[plugin]))
    assert (await plugin_market.get_plugin(1, db, SimpleNamespace(id=1))).id == 1

    monkeypatch.setattr(MarketPlugin, "get_by_github_url", AsyncMock(return_value=None))
    monkeypatch.setattr(
        plugin_market,
        "fetch_github_repo_info",
        AsyncMock(
            return_value=SimpleNamespace(
                success=True, repo_name="Fetched", description="Fetched description", author="Fetched author"
            )
        ),
    )
    monkeypatch.setattr(plugin_market, "get_effective_github_token", AsyncMock(return_value="token"))
    db = _Db()
    async def refresh_plugin(value):
        value.id = 99
        value.created_at = datetime.now(UTC)
        value.updated_at = datetime.now(UTC)

    db.refresh.side_effect = refresh_plugin
    created = await plugin_market.create_plugin(
        plugin_market.MarketPluginCreate(github_url="https://github.com/acme/new", category="utility"),
        db,
        _admin(),
    )
    assert created.title == "Fetched"
    assert db.add.call_count == 1

    monkeypatch.setattr(MarketPlugin, "get_by_github_url", AsyncMock(return_value=plugin))
    with pytest.raises(HTTPException) as caught:
        await plugin_market.create_plugin(
            plugin_market.MarketPluginCreate(github_url=plugin.github_url), db, _admin()
        )
    assert caught.value.status_code == 409

    monkeypatch.setattr(MarketPlugin, "get_by_github_url", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await plugin_market.create_plugin(
            plugin_market.MarketPluginCreate(github_url="https://github.com/acme/invalid", category="bad"),
            db,
            _admin(),
        )
    assert caught.value.status_code == 400

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(plugin_market, "validate_dependencies", AsyncMock())
    updated = await plugin_market.update_plugin(
        1,
        plugin_market.MarketPluginUpdate(
            title="Changed", description="New", author="New author", version="2", category="admin",
            tags="a,b", is_recommended=True, icon_url="https://x/icon", dependencies="2",
            custom_install_path="addons",
        ),
        db,
        _admin(),
    )
    assert updated.title == "Changed"
    assert updated.category == PluginCategory.ADMIN
    assert plugin.dependencies == "2"

    with pytest.raises(HTTPException) as caught:
        await plugin_market.update_plugin(
            1, plugin_market.MarketPluginUpdate(category="bad"), db, _admin()
        )
    assert caught.value.status_code == 400
    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await plugin_market.update_plugin(1, plugin_market.MarketPluginUpdate(title="x"), db, _admin())
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_market_dependency_conflict_release_and_simple_routes(monkeypatch):
    plugin, other = _plugin(), _plugin(2)
    db = _Db(rows=[])
    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    assert (await plugin_market.get_plugin_conflict_rules(1, db, SimpleNamespace())) == []

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=None))
    with pytest.raises(HTTPException):
        await plugin_market.get_plugin_conflict_rules(1, db, SimpleNamespace())

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=[other]))
    rule_request = plugin_market.PluginConflictRulesUpdate(
        rules=[PluginConflictRuleInput(other_plugin_id=2, severity="warning", reason=" optional ")]
    )
    created = await plugin_market.replace_plugin_conflict_rules(1, rule_request, db, _admin())
    assert created[0].plugin_a_id == 1
    assert created[0].plugin_b_id == 2
    assert created[0].reason == "optional"
    for request, expected in (
        (plugin_market.PluginConflictRulesUpdate(rules=[PluginConflictRuleInput(other_plugin_id=1, severity="hard", reason="self")]), 422),
        (plugin_market.PluginConflictRulesUpdate(rules=[PluginConflictRuleInput(other_plugin_id=2, severity="hard", reason="a"), PluginConflictRuleInput(other_plugin_id=2, severity="hard", reason="b")]), 422),
    ):
        with pytest.raises(HTTPException) as caught:
            await plugin_market.replace_plugin_conflict_rules(1, request, db, _admin())
        assert caught.value.status_code == expected
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=[]))
    with pytest.raises(HTTPException) as caught:
        await plugin_market.replace_plugin_conflict_rules(
            1,
            plugin_market.PluginConflictRulesUpdate(rules=[PluginConflictRuleInput(other_plugin_id=2, severity="hard", reason="missing")]),
            db,
            _admin(),
        )
    assert caught.value.status_code == 422

    github_module = importlib.import_module("api.routes.github_plugins")
    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    releases = AsyncMock(return_value={"releases": []})
    monkeypatch.setattr(github_module, "get_github_releases", releases)
    result = await plugin_market.get_plugin_releases(1, server_id=4, count=3, db=db, current_user=SimpleNamespace())
    assert result == {"releases": []}
    releases.assert_awaited_once()

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=None))
    with pytest.raises(HTTPException):
        await plugin_market.get_plugin_releases(1, server_id=None, count=5, db=db, current_user=SimpleNamespace())

    monkeypatch.setattr(MarketPlugin, "search_plugins", AsyncMock(return_value=([plugin, other], 2)))
    result = await plugin_market.list_plugins_for_dependencies(
        exclude_id=None, search=None, db=db, current_user=_admin()
    )
    assert len(result["plugins"]) == 2


@pytest.mark.asyncio
async def test_market_install_helpers_and_install_route_short_circuits(monkeypatch):
    plugin = _plugin()
    server = SimpleNamespace(id=4, github_proxy=None)
    user = SimpleNamespace(id=1)
    db = _Db()
    monkeypatch.setattr(plugin_market, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(plugin_market, "build_plugin_install_plan", AsyncMock(return_value={"dependencies": False, "plan_hash": "hash", "steps": [], "warnings": [], "plugin": {}}))
    monkeypatch.setattr(plugin_market, "validate_plugin_plan_acknowledgements", Mock())
    monkeypatch.setattr(plugin_market, "_check_plugin_ssh", AsyncMock(return_value=(False, "offline")))
    result = await plugin_market.install_plugin(
        1,
        server_id=4,
        download_url="https://github.com/acme/repo/releases/download/v1/a.zip",
        exclude_dirs=[],
        exclude_files=[],
        install_dependencies=False,
        acknowledge_warning_rule_ids=[],
        upgrade_mode=False,
        db=db,
        current_user=user,
        _operation_server=object(),
    )
    assert result.success is False and "offline" in result.message

    monkeypatch.setattr(plugin_market, "_check_plugin_ssh", AsyncMock(return_value=(True, "ok")))
    monkeypatch.setattr(plugin_market, "_resolve_market_asset", AsyncMock(return_value=(None, None, None, None, "no asset", None)))
    result = await plugin_market.install_plugin(
        1, server_id=4, download_url=None, exclude_dirs=[], exclude_files=[], install_dependencies=False,
        acknowledge_warning_rule_ids=[], upgrade_mode=False, db=db, current_user=user, _operation_server=object()
    )
    assert result.success is False and result.message == "no asset"

    monkeypatch.setattr(plugin_market, "_resolve_market_asset", AsyncMock(return_value=("https://x/a.zip", None, None, "a.zip", None, None)))
    monkeypatch.setattr(plugin_market, "_validate_latest_target_plan", AsyncMock(return_value="changed"))
    result = await plugin_market.install_plugin(
        1, server_id=4, download_url=None, exclude_dirs=[], exclude_files=[], install_dependencies=False,
        acknowledge_warning_rule_ids=[], upgrade_mode=False, db=db, current_user=user, _operation_server=object()
    )
    assert "rules changed" in result.message

    monkeypatch.setattr(plugin_market, "_validate_latest_target_plan", AsyncMock(return_value=None))
    execute = AsyncMock(return_value=plugin_market.GitHubPluginInstallResponse(success=True, message="installed"))
    monkeypatch.setattr(plugin_market, "_execute_market_install", execute)
    result = await plugin_market.install_plugin(
        1, server_id=4, download_url="https://github.com/acme/repo/releases/download/v1/a.zip",
        exclude_dirs=[], exclude_files=[], install_dependencies=False, acknowledge_warning_rule_ids=[],
        upgrade_mode=True, db=db, current_user=user, _operation_server=object()
    )
    assert result.success is True
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_market_repo_and_archive_wrappers_cover_delegate_errors(monkeypatch):
    plugin = _plugin()
    server = SimpleNamespace(id=4, github_proxy=None)
    db = _Db()
    user = SimpleNamespace(id=1)
    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(plugin_market, "get_server_for_user", AsyncMock(return_value=server))
    github_module = importlib.import_module("api.routes.github_plugins")
    analyze = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(github_module, "analyze_archive", analyze)
    monkeypatch.setattr(plugin_market, "http_helper", SimpleNamespace(get=AsyncMock(return_value=(True, {"assets": [{"name": "win.zip"}, {"name": "linux.zip", "browser_download_url": "https://x/linux.zip"}]}, None))))
    monkeypatch.setattr(plugin_market, "get_effective_github_token", AsyncMock(return_value="token"))
    result = await plugin_market.analyze_plugin_archive(1, server_id=4, download_url=None, db=db, current_user=user)
    assert result == {"ok": True}
    analyze.assert_awaited_once()

    monkeypatch.setattr(plugin_market, "http_helper", SimpleNamespace(get=AsyncMock(return_value=(False, None, "forbidden"))))
    with pytest.raises(HTTPException) as caught:
        await plugin_market.analyze_plugin_archive(1, server_id=4, download_url=None, db=db, current_user=user)
    assert caught.value.status_code == 500

    fetch = AsyncMock(return_value=GitHubRepoInfo(success=True, repo_name="repo", author="acme"))
    monkeypatch.setattr(plugin_market, "fetch_github_repo_info", fetch)
    monkeypatch.setattr(plugin_market, "get_effective_github_token", AsyncMock(return_value="token"))
    result = await plugin_market.fetch_repo_info(
        github_url="https://github.com/acme/repo", db=db, current_user=_admin()
    )
    assert result.success is True

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=None))
    with pytest.raises(HTTPException):
        await plugin_market.uninstall_market_plugin(
            1,
            4,
            plugin_market.PluginUninstallRequest(files_to_delete=[]),
            db,
            user,
            object(),
        )
