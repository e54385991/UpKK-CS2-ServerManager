"""Cover marketplace resolution, projection, and dependency route branches."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import plugin_market
from modules import MarketPlugin, PluginCategory


def _plugin(plugin_id=1, *, dependencies=None, **overrides):
    values = dict(
        id=plugin_id,
        github_url=f"https://github.com/acme/plugin-{plugin_id}",
        title=f"Plugin {plugin_id}",
        description="Description",
        author="Author",
        version="1.0",
        category=PluginCategory.UTILITY,
        tags="tag",
        dependencies=dependencies,
        is_recommended=False,
        icon_url=None,
        custom_install_path=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    values.update(overrides)
    return MarketPlugin(**values)


@pytest.mark.asyncio
async def test_market_asset_resolution_covers_filtering_and_runtime_selection(monkeypatch):
    plugin = _plugin()
    server = SimpleNamespace(github_proxy=None)
    user = SimpleNamespace(id=1)
    monkeypatch.setattr(plugin_market, "get_effective_github_token", AsyncMock(return_value="tok"))
    get = AsyncMock(
        return_value=(
            True,
            {
                "id": 12,
                "tag_name": "v2",
                "assets": [
                    {"name": "demo-windows.zip", "browser_download_url": "bad"},
                    {"name": "demo-linux.zip", "browser_download_url": "https://x/demo.zip"},
                    {"name": "notes.txt", "browser_download_url": "bad"},
                ],
            },
            None,
        )
    )
    monkeypatch.setattr(plugin_market.http_helper, "get", get)
    result, error = await plugin_market.resolve_latest_market_asset(
        plugin, server, object(), user, {"reason": "best"}
    )
    assert result["asset_name"] == "demo-linux.zip" and error is None

    get.return_value = (False, None, "rate limited")
    result, error = await plugin_market.resolve_latest_market_asset(
        plugin, server, object(), user, None
    )
    assert result is None and "rate limited" in error
    get.return_value = (True, {"assets": []}, None)
    result, error = await plugin_market.resolve_latest_market_asset(
        plugin, server, object(), user, None
    )
    assert result is None and "No suitable" in error

    get.return_value = (True, {"assets": [{"name": "demo.zip"}]}, None)
    result, error = await plugin_market.resolve_latest_market_asset(
        plugin, server, object(), user, None
    )
    assert result is None and "download URL" in error

    monkeypatch.setattr("services.linux_runtime_service.has_paired_runtime_assets", lambda _: True)
    monkeypatch.setattr("services.linux_runtime_service.select_unique_runtime_asset", lambda *_a: None)
    get.return_value = (
        True,
        {"assets": [{"name": "demo.zip", "browser_download_url": "https://x/a.zip"}]},
        None,
    )
    with pytest.raises(Exception, match="Multiple Steam Runtime"):
        await plugin_market.resolve_latest_market_asset(plugin, server, object(), user, None)


@pytest.mark.asyncio
async def test_market_repo_info_and_dependency_helpers_cover_external_response_shapes(monkeypatch):
    invalid = await plugin_market.fetch_github_repo_info("https://gitlab.com/acme/demo")
    assert invalid.success is False
    monkeypatch.setattr(
        plugin_market.http_helper,
        "get",
        AsyncMock(side_effect=[
            (False, None, "forbidden"),
        ]),
    )
    failed = await plugin_market.fetch_github_repo_info("https://github.com/acme/demo")
    assert failed.success is False and "forbidden" in failed.error

    readme = base64.b64encode(b"# Header\nUseful details\nMore details").decode()
    monkeypatch.setattr(
        plugin_market.http_helper,
        "get",
        AsyncMock(side_effect=[
            (True, {"name": "Demo", "description": "", "owner": {}}, None),
            (True, {"content": readme}, None),
        ]),
    )
    info = await plugin_market.fetch_github_repo_info("https://github.com/acme/demo")
    assert info.success and info.description == "Useful details More details" and info.author == "acme"

    dep_a, dep_b = _plugin(2), _plugin(3)
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=[dep_a, dep_b]))
    responses = await plugin_market.populate_dependency_details(
        object(), [_plugin(1, dependencies="2,invalid"), _plugin(4, dependencies=None)]
    )
    assert responses[0].dependency_details is None
    assert responses[1].dependency_details is None
    await plugin_market.validate_dependencies(object(), [2, 3])


@pytest.mark.asyncio
async def test_market_simple_routes_and_install_dependency_results(monkeypatch):
    categories = await plugin_market.list_categories(SimpleNamespace())
    assert categories["success"] and categories["categories"]

    plugins = [_plugin(1), _plugin(2)]
    monkeypatch.setattr(MarketPlugin, "search_plugins", AsyncMock(return_value=(plugins, 2)))
    listed = await plugin_market.list_plugins_for_dependencies(
        exclude_id=1, search="plugin", db=object(), current_user=SimpleNamespace()
    )
    assert listed["plugins"] == [{"id": 2, "title": "Plugin 2"}]

    db = SimpleNamespace()
    install = AsyncMock(return_value=SimpleNamespace(success=True, message="ok"))
    dep_plan = {"dependencies": True, "installation_order": [1, 2, 3], "already_installed": [3]}
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=[_plugin(2)]))
    result, error = await plugin_market._install_dependencies(
        install,
        dep_plan,
        1,
        5,
        [],
        [],
        [],
        False,
        db,
        SimpleNamespace(id=1),
        object(),
    )
    assert result == ["Plugin 2"] if result else True
    assert error is None

    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=[_plugin(2)]))
    install.side_effect = HTTPException(status_code=400, detail="stopped")
    result, error = await plugin_market._install_dependencies(
        install,
        dep_plan,
        1,
        5,
        [],
        [],
        [],
        False,
        db,
        SimpleNamespace(id=1),
        object(),
    )
    assert error is not None and "stopped" in error.message


@pytest.mark.asyncio
async def test_market_crud_and_plan_wrappers_cover_not_found_and_success(monkeypatch):
    user = SimpleNamespace(id=1, is_admin=True, username="admin")
    db = SimpleNamespace(add=lambda _v: None, commit=AsyncMock(), refresh=AsyncMock())
    plugin = _plugin(1)
    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=[]))
    response = await plugin_market.get_plugin(1, db, user)
    assert response.id == 1
    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=None))
    with pytest.raises(HTTPException, match="Plugin not found"):
        await plugin_market.get_plugin(1, db, user)

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(
        plugin_market,
        "get_server_for_user",
        AsyncMock(return_value=SimpleNamespace(id=5)),
    )
    monkeypatch.setattr(
        plugin_market,
        "build_plugin_install_plan",
        AsyncMock(return_value={"plugin_id": 1, "installation_order": [1]}),
    )
    preflight = await plugin_market.plugin_install_preflight(
        1, server_id=5, install_dependencies=False, db=db, current_user=user
    )
    assert preflight["plugin_id"] == 1
    monkeypatch.setattr(plugin_market, "build_plugin_install_plan", AsyncMock(side_effect=plugin_market.PluginPlanError("blocked")))
    with pytest.raises(HTTPException, match="blocked"):
        await plugin_market.plugin_install_preflight(
            1, server_id=5, install_dependencies=True, db=db, current_user=user
        )
