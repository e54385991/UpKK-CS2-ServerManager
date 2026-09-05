"""覆盖插件市场中尚未触达的权限、依赖、资产和清理分支。"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.routes import plugin_market
from modules import MarketPlugin, PluginCategory, Server
from services.linux_runtime_service import RuntimeSelectionRequired


def _plugin(plugin_id=1, **overrides):
    values = dict(
        id=plugin_id,
        github_url=f"https://github.com/acme/plugin-{plugin_id}",
        title=f"Plugin {plugin_id}",
        description="Description",
        author="Author",
        version="1.0",
        category=PluginCategory.UTILITY,
        tags="tag",
        dependencies=None,
        is_recommended=False,
        icon_url=None,
        custom_install_path=None,
        download_count=0,
        install_count=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    values.update(overrides)
    return MarketPlugin(**values)


class _Db:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _value):
        return None

    async def delete(self, _value):
        return None

    async def execute(self, _statement):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: list(self.rows)),
            scalar_one_or_none=lambda: self.rows[0] if self.rows else None,
        )


@pytest.mark.asyncio
async def test_market_server_and_dependency_helpers_cover_all_outcomes(monkeypatch):
    server = SimpleNamespace(id=4)
    admin_lookup = AsyncMock(return_value=server)
    owner_lookup = AsyncMock(return_value=server)
    monkeypatch.setattr(Server, "get_by_id", admin_lookup)
    monkeypatch.setattr(Server, "get_by_id_and_user", owner_lookup)
    db = _Db()
    assert (
        await plugin_market.get_server_for_user(4, db, SimpleNamespace(id=1, is_admin=True))
        is server
    )
    assert (
        await plugin_market.get_server_for_user(4, db, SimpleNamespace(id=1, is_admin=False))
        is server
    )
    assert db.commits == 2
    admin_lookup.return_value = None
    with pytest.raises(HTTPException) as missing:
        await plugin_market.get_server_for_user(4, db, SimpleNamespace(id=1, is_admin=True))
    assert missing.value.status_code == 404

    dep = _plugin(2)
    plan = {"dependencies": True, "installation_order": [1, 2, 3], "already_installed": [3]}
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=[dep]))
    install = AsyncMock(return_value=SimpleNamespace(success=True, message="ok"))
    installed, error = await plugin_market._install_dependencies(
        install, plan, 1, 4, [], [], [], False, db, SimpleNamespace(id=1), object()
    )
    assert installed == [dep.title] and error is None

    install.return_value = SimpleNamespace(success=False, message="bad")
    installed, error = await plugin_market._install_dependencies(
        install,
        {**plan, "installation_order": [1, 2]},
        1,
        4,
        [],
        [],
        [],
        False,
        db,
        SimpleNamespace(id=1),
        object(),
    )
    assert installed == [] and "failed" in error.message

    install.side_effect = HTTPException(status_code=409, detail="conflict")
    installed, error = await plugin_market._install_dependencies(
        install,
        {**plan, "installation_order": [1, 2]},
        1,
        4,
        [],
        [],
        [],
        False,
        db,
        SimpleNamespace(id=1),
        object(),
    )
    assert installed == [] and "stopped" in error.message

    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(side_effect=ValueError("bad ids")))
    installed, error = await plugin_market._install_dependencies(
        install,
        {**plan, "installation_order": [1, 2]},
        1,
        4,
        [],
        [],
        [],
        False,
        db,
        SimpleNamespace(id=1),
        object(),
    )
    assert installed == [] and error is None


@pytest.mark.asyncio
async def test_market_asset_validation_and_install_counter_cleanup(monkeypatch):
    plugin = _plugin()
    server = SimpleNamespace(id=4, github_proxy=None)
    user = SimpleNamespace(id=1)
    direct = await plugin_market._resolve_market_asset(
        plugin,
        server,
        _Db(),
        user,
        "https://github.com/a/b/releases/download/v1/a.zip",
        "a.zip",
        None,
    )
    assert direct[0].endswith("a.zip")

    monkeypatch.setattr(
        plugin_market,
        "resolve_latest_market_asset",
        AsyncMock(side_effect=RuntimeSelectionRequired("choose")),
    )
    with pytest.raises(HTTPException) as conflict:
        await plugin_market._resolve_market_asset(plugin, server, _Db(), user, None, None, None)
    assert conflict.value.status_code == 409
    monkeypatch.setattr(
        plugin_market, "resolve_latest_market_asset", AsyncMock(return_value=(None, "no asset"))
    )
    resolved = await plugin_market._resolve_market_asset(
        plugin, server, _Db(), user, None, None, None
    )
    assert resolved[0] is None and resolved[4] == "no asset"

    db = _Db()
    result = SimpleNamespace(success=True, message="installed")
    monkeypatch.setattr(plugin_market, "install_github_plugin", AsyncMock(return_value=result))
    monkeypatch.setattr("services.plugin_auto_update_service.upsert_managed_plugin", AsyncMock())
    monkeypatch.setattr("services.plugin_auto_update_service.derive_asset_glob", lambda *_: "*.zip")
    exclusions = Mock(side_effect=lambda files: [*files, "config.json"])
    monkeypatch.setattr(plugin_market, "apply_upgrade_mode_exclusions", exclusions)
    saved_commit = db.commit
    db.commit = AsyncMock(side_effect=[None, RuntimeError("counter write"), None])
    success = await plugin_market._execute_market_install(
        plugin,
        4,
        server,
        "https://github.com/acme/repo/releases/download/v1/a.zip",
        "9",
        "v1",
        "a.zip",
        [],
        ["readme"],
        True,
        db,
        user,
        ["Dependency"],
    )
    assert success.success is True and "Dependencies" in success.message
    assert db.rollbacks == 1
    assert exclusions.called

    failed = SimpleNamespace(success=False, message="remote failed")
    monkeypatch.setattr(plugin_market, "install_github_plugin", AsyncMock(return_value=failed))
    result = await plugin_market._execute_market_install(
        plugin,
        4,
        server,
        "https://github.com/acme/repo/releases/download/v1/a.zip",
        None,
        None,
        None,
        [],
        [],
        False,
        _Db(),
        user,
        [],
    )
    assert result is failed
    monkeypatch.setattr(
        plugin_market, "install_github_plugin", AsyncMock(side_effect=RuntimeError("boom"))
    )
    result = await plugin_market._execute_market_install(
        plugin,
        4,
        server,
        "https://github.com/acme/repo/releases/download/v1/a.zip",
        None,
        None,
        None,
        [],
        [],
        False,
        _Db(),
        user,
        ["Dep"],
    )
    assert result.success is False and "Installation error" in result.message
    with pytest.raises(HTTPException):
        await plugin_market._execute_market_install(
            plugin, 4, server, None, None, None, None, [], [], False, _Db(), user, []
        )
    del saved_commit


@pytest.mark.asyncio
async def test_market_readme_dependencies_and_admin_crud_errors(monkeypatch):
    plugin = _plugin(1, dependencies="2,invalid")
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=[]))
    responses = await plugin_market.populate_dependency_details(_Db(), [plugin])
    assert responses[0].dependency_details is None

    monkeypatch.setattr(
        plugin_market.http_helper,
        "get",
        AsyncMock(return_value=(True, {"name": "Demo", "description": "desc"}, None)),
    )
    info = await plugin_market.fetch_github_repo_info("https://github.com/acme/demo")
    assert info.success and info.description == "desc"
    bad_readme = base64.b64encode(b"\xff").decode()
    monkeypatch.setattr(
        plugin_market.http_helper,
        "get",
        AsyncMock(
            side_effect=[
                (True, {"name": "Demo", "description": "desc"}, None),
                (True, {"content": bad_readme}, None),
            ]
        ),
    )
    assert (await plugin_market.fetch_github_repo_info("https://github.com/acme/demo")).success

    db = _Db()
    monkeypatch.setattr(MarketPlugin, "get_by_github_url", AsyncMock(return_value=None))
    monkeypatch.setattr(
        plugin_market,
        "fetch_github_repo_info",
        AsyncMock(return_value=SimpleNamespace(success=False)),
    )
    with pytest.raises(HTTPException) as invalid_category:
        await plugin_market.create_plugin(
            plugin_market.MarketPluginCreate(
                github_url="https://github.com/acme/new", category="bad"
            ),
            db,
            SimpleNamespace(id=1, is_admin=True, username="admin"),
        )
    assert invalid_category.value.status_code == 400

    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(
        plugin_market, "validate_dependencies", AsyncMock(side_effect=ValueError("invalid"))
    )
    with pytest.raises(HTTPException) as bad_dep:
        await plugin_market.update_plugin(
            1,
            plugin_market.MarketPluginUpdate(dependencies="x"),
            db,
            SimpleNamespace(username="admin"),
        )
    assert bad_dep.value.status_code == 400

    monkeypatch.setattr(plugin_market, "delete_market_plugin", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as not_found:
        await plugin_market.delete_plugin(1, db, SimpleNamespace(username="admin"))
    assert not_found.value.status_code == 404
    deleted = _plugin(1)
    monkeypatch.setattr(plugin_market, "delete_market_plugin", AsyncMock(return_value=deleted))
    assert (await plugin_market.delete_plugin(1, db, SimpleNamespace(username="admin"))).success


@pytest.mark.asyncio
async def test_market_archive_asset_selection_and_uninstall_tracking(monkeypatch):
    plugin = _plugin()
    server = SimpleNamespace(id=4, github_proxy=None)
    user = SimpleNamespace(id=1)
    db = _Db()
    monkeypatch.setattr(MarketPlugin, "get_by_id", AsyncMock(return_value=plugin))
    monkeypatch.setattr(plugin_market, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(
        plugin_market.http_helper,
        "get",
        AsyncMock(
            return_value=(True, {"assets": [{"name": "windows.zip"}, {"name": "linux.zip"}]}, None)
        ),
    )
    with pytest.raises(HTTPException) as no_asset:
        await plugin_market.analyze_plugin_archive(
            1, server_id=4, download_url=None, db=db, current_user=user
        )
    assert no_asset.value.status_code == 404

    plugin.github_url = "https://gitlab.com/acme/demo"
    with pytest.raises(HTTPException) as invalid_url:
        await plugin_market.analyze_plugin_archive(
            1, server_id=4, download_url=None, db=db, current_user=user
        )
    assert invalid_url.value.status_code == 400

    plugin.github_url = "https://github.com/acme/demo"
    archive = AsyncMock(return_value={"files": []})
    monkeypatch.setattr("api.routes.github_plugins.analyze_archive", archive)
    result = await plugin_market.analyze_plugin_archive(
        1, server_id=4, download_url="https://x/a.zip", db=db, current_user=user
    )
    assert result == {"files": []}

    tracked = SimpleNamespace()
    db = _Db([tracked])
    monkeypatch.setattr(
        "api.routes.github_plugins.uninstall_plugin",
        AsyncMock(return_value=SimpleNamespace(success=True, message="ok")),
    )
    result = await plugin_market.uninstall_market_plugin(
        1, 4, plugin_market.PluginUninstallRequest(files_to_delete=[]), db, user, object()
    )
    assert result.success and db.rollbacks == 0
    assert db.commits == 1
