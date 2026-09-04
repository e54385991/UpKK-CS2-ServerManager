"""覆盖托管插件自动更新配置接口的校验和入队分支。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import plugin_auto_update as routes
from modules import ManagedPlugin, ManagedPluginCreate, ManagedPluginUpdate, PluginAutoUpdateSettings
from services.server_operation_hub import ServerOperationConflict


class _Rows:
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
    def __init__(self, results=(), get_value=None):
        self.results = list(results)
        self.get_value = get_value
        self.added = []
        self.deleted = []
        self.commit = AsyncMock()
        async def refresh(value):
            if isinstance(value, ManagedPlugin) and value.id is None:
                value.id = 99
        self.refresh = AsyncMock(side_effect=refresh)

    async def execute(self, _query):
        return self.results.pop(0) if self.results else _Rows()

    async def get(self, *_args):
        return self.get_value

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)


def _server(**overrides):
    values = dict(
        id=3, user_id=7, enable_plugin_auto_update=False,
        plugin_update_check_interval_hours=1.0, last_plugin_update_check=None,
        enable_plugin_post_update_commands=False, plugin_post_update_command_ids=[],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _plugin(**overrides):
    values = dict(
        id=4, server_id=3, source_type="github", source_key="acme/plugin",
        display_name="Plugin", repo_url="https://github.com/acme/plugin",
        market_plugin_id=None, framework_key=None, installed_release_id=None,
        installed_version="unknown", asset_glob="*.zip", custom_install_path=None,
        exclude_dirs=[], exclude_files=[], auto_update_enabled=True,
        backup_before_update=True, restart_after_update=True,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    values.update(overrides)
    return ManagedPlugin(**values)


@pytest.mark.asyncio
async def test_auto_update_helpers_and_configuration(monkeypatch):
    assert routes.normalize_command_ids([1, "1", 2, 1]) == [1, 2]
    assert routes.normalize_command_ids([]) == []
    user = SimpleNamespace(id=7, is_admin=False)
    server = _server()
    monkeypatch.setattr(routes.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    assert await routes.owned_server(_Db(), 3, user) is server
    monkeypatch.setattr(routes.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    with pytest.raises(HTTPException):
        await routes.owned_server(_Db(), 3, user)
    monkeypatch.setattr(routes.Server, "get_by_id_and_user", AsyncMock(return_value=server))

    plugin = _plugin()
    assert await routes.owned_plugin(_Db([_Rows(scalar=plugin)]), 3, 4) is plugin
    with pytest.raises(HTTPException):
        await routes.owned_plugin(_Db([_Rows(scalar=None)]), 3, 4)

    commands_db = _Db([_Rows([SimpleNamespace(id=1), SimpleNamespace(id=2)])])
    assert await routes.validate_post_update_commands(commands_db, server, user, [1, 2, 1]) == [1, 2]
    with pytest.raises(HTTPException, match="Unknown"):
        await routes.validate_post_update_commands(_Db([_Rows([SimpleNamespace(id=1)])]), server, user, [1, 2])

    config = await routes.get_configuration(
        3, _Db([_Rows([plugin])]), server_user := user
    )
    assert config.plugins[0].id == 4

    request = PluginAutoUpdateSettings(
        enable_plugin_auto_update=True, plugin_update_check_interval_hours=2,
        enable_plugin_post_update_commands=True, plugin_post_update_command_ids=[1],
    )
    db = _Db([_Rows([SimpleNamespace(id=1)]), _Rows([plugin])])
    result = await routes.update_settings(3, request, db, user)
    assert result.enable_plugin_auto_update is True


@pytest.mark.asyncio
async def test_register_update_unmanage_and_run_routes(monkeypatch):
    user = SimpleNamespace(id=7, is_admin=False)
    server = _server()
    monkeypatch.setattr(routes, "owned_server", AsyncMock(return_value=server))
    db = _Db([_Rows(scalar=None)])
    plugin = await routes.register_plugin(
        3,
        ManagedPluginCreate(
            source_type="github", source_key="acme/plugin", display_name="Plugin",
            repo_url="https://github.com/acme/plugin", asset_glob="*.zip",
        ),
        db,
        user,
    )
    assert plugin.source_key == "acme/plugin"

    with pytest.raises(HTTPException):
        await routes.register_plugin(
            3, ManagedPluginCreate(source_type="framework", framework_key="unknown", display_name="x"), _Db(), user
        )
    with pytest.raises(HTTPException):
        await routes.register_plugin(
            3, ManagedPluginCreate(source_type="market", market_plugin_id=99, display_name="x"), _Db(get_value=None), user
        )
    market = SimpleNamespace(id=99, github_url="https://github.com/acme/market", title="Market", custom_install_path=None)
    with pytest.raises(HTTPException):
        await routes.register_plugin(
            3, ManagedPluginCreate(source_type="market", market_plugin_id=99, display_name="x"), _Db(get_value=market), user
        )
    duplicate_db = _Db([_Rows(scalar=SimpleNamespace())])
    with pytest.raises(HTTPException) as exc:
        await routes.register_plugin(
            3,
            ManagedPluginCreate(source_type="github", display_name="x", repo_url="https://github.com/acme/x", asset_glob="*.zip"),
            duplicate_db,
            user,
        )
    assert exc.value.status_code == 409

    plugin = _plugin()
    monkeypatch.setattr(routes, "owned_plugin", AsyncMock(return_value=plugin))
    updated = await routes.update_plugin(3, 4, ManagedPluginUpdate(auto_update_enabled=False), _Db(), user)
    assert updated.auto_update_enabled is False
    db = _Db()
    response = await routes.unmanage_plugin(3, 4, db, user)
    assert response.success and db.deleted

    monkeypatch.setattr(routes, "reject_stuck_lock_unless_active", AsyncMock())
    monkeypatch.setattr(routes, "enqueue_plugin_auto_update", AsyncMock())
    assert (await routes.run_now(3, _Db(), user)).success
    monkeypatch.setattr(routes, "enqueue_plugin_auto_update", AsyncMock(side_effect=ServerOperationConflict("busy")))
    with pytest.raises(HTTPException) as exc:
        await routes.run_now(3, _Db(), user)
    assert exc.value.status_code == 409

    monkeypatch.setattr(routes, "owned_plugin", AsyncMock(return_value=plugin))
    monkeypatch.setattr(routes, "enqueue_plugin_auto_update", AsyncMock())
    assert (await routes.test_plugin_update(3, 4, _Db(), user)).success
    monkeypatch.setattr(routes, "enqueue_plugin_auto_update", AsyncMock(side_effect=ServerOperationConflict("busy")))
    with pytest.raises(HTTPException):
        await routes.test_plugin_update(3, 4, _Db(), user)

    monkeypatch.setattr(routes.plugin_auto_update_service, "get_status", AsyncMock(return_value={"running": False}))
    assert (await routes.get_run_status(3, _Db(), user))["running"] is False
