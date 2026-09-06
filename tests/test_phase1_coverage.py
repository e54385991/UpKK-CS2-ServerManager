"""Deterministic coverage for small services and legacy compatibility routes."""

from __future__ import annotations

import asyncio
import importlib
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile

from api.routes import plugins as legacy_plugins
from api.routes import settings as legacy_settings
from api.routes import user_settings
from modules import (
    GlobalSettingsUpdate,
    PluginCategory,
    PluginCreate,
    PluginInstallRequest,
    UserSettingsUpdate,
)
from modules.schemas.legacy import AutoRestartSettings
from services import ai_retention_service as retention_module
from services import github_service
from services import system_info_helper as system_info_module


class _ScalarResult:
    def __init__(self, value=None, rows=None):
        self.value = value
        self.rows = list(rows or [])

    def scalar_one_or_none(self):
        return self.value

    def scalar(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, *, execute_results=None, get_results=None):
        self.execute_results = list(execute_results or [])
        self.get_results = list(get_results or [])
        self.added = []
        self.deleted = []
        self.commits = 0
        self.refreshes = []

    async def execute(self, _query):
        if not self.execute_results:
            return _ScalarResult()
        result = self.execute_results.pop(0)
        return result if isinstance(result, _ScalarResult) else _ScalarResult(result)

    async def get(self, _model, _item_id):
        return self.get_results.pop(0) if self.get_results else None

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = len(self.added) + 1
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        self.refreshes.append(value)

    async def delete(self, value):
        self.deleted.append(value)


class _DbContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return None


def _legacy_plugin(**overrides):
    values = {
        "id": 7,
        "name": "demo",
        "display_name": "Demo",
        "description": "A plugin",
        "category": PluginCategory.UTILITY,
        "version": "1.2.3",
        "download_url": "https://example.test/demo.tar.gz",
        "author": "Tester",
        "homepage": None,
        "dependencies": None,
        "install_path": "addons/plugins",
        "config_required": False,
        "enabled": True,
    }
    values.update(overrides)
    from modules.models import Plugin

    return Plugin(**values)


def _legacy_server(**overrides):
    values = {
        "id": 3,
        "user_id": 11,
        "game_directory": "/srv/cs2",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_contract_indexes_and_protocol_exports_are_importable():
    requests = importlib.import_module("api.contracts.v1.requests")
    responses = importlib.import_module("api.contracts.v1.responses")
    ports = importlib.import_module("services.ports")

    assert "ServerCreateRequest" in requests.__all__
    assert "ServerDetail" in responses.__all__
    assert set(ports.__all__) == {
        "AsyncStream",
        "Cleanup",
        "CoordinationStore",
        "HTTPTransport",
        "SSHLeaseProvider",
        "SessionFactory",
    }


@pytest.mark.asyncio
async def test_github_service_parses_fetches_and_classifies_repositories(monkeypatch):
    assert github_service.parse_github_url("https://github.com/acme/demo/releases") == (
        "acme",
        "demo",
    )
    with pytest.raises(ValueError, match="Invalid GitHub"):
        github_service.parse_github_url("https://gitlab.com/acme/demo")

    get = AsyncMock(
        return_value=(
            True,
            {
                "name": "Demo",
                "description": "",
                "owner": {"login": "acme"},
                "topics": ["utility"],
                "html_url": "https://github.com/acme/demo",
                "language": "C#",
                "stargazers_count": 4,
            },
            None,
        )
    )
    monkeypatch.setattr(github_service.http_helper, "get", get)
    result = await github_service.fetch_github_repo_info("https://github.com/acme/demo")
    assert result["name"] == "acme-demo"
    assert result["description"] == "Demo - GitHub repository"
    assert result["stars"] == 4
    get.assert_awaited_once()

    monkeypatch.setattr(
        github_service.http_helper,
        "get",
        AsyncMock(return_value=(False, None, "rate limited")),
    )
    with pytest.raises(ValueError, match="rate limited"):
        await github_service.fetch_github_repo_info("https://github.com/acme/demo")

    monkeypatch.setattr(
        github_service.http_helper,
        "get",
        AsyncMock(return_value=(True, None, None)),
    )
    with pytest.raises(ValueError, match="Empty response"):
        await github_service.fetch_github_repo_info("https://github.com/acme/demo")

    monkeypatch.setattr(
        github_service.http_helper,
        "get",
        AsyncMock(side_effect=RuntimeError("network")),
    )
    with pytest.raises(ValueError, match="Unexpected error"):
        await github_service.fetch_github_repo_info("https://github.com/acme/demo")

    assert github_service.determine_category({"topics": ["metamod"]}) == "依赖"
    assert github_service.determine_category({"description": "server monitor"}) == "功能"
    assert github_service.determine_category({"name": "fun-game"}) == "娱乐"
    assert github_service.determine_category({}) == "功能"


@pytest.mark.asyncio
async def test_system_info_helper_handles_success_failure_and_batches(monkeypatch):
    service = system_info_module.SystemInfoHelper()
    server = SimpleNamespace(id=8)
    get_disk = AsyncMock(side_effect=[(True, {"used_gb": 1}), (False, None), (True, {})])
    monkeypatch.setattr(system_info_module.disk_space_service, "get_disk_space", get_disk)

    assert await service.get_system_info(server) == {
        "server_id": 8,
        "disk_space": {"used_gb": 1},
        "success": True,
    }
    assert await service.get_disk_space(server) is None
    assert await service.get_system_info(server) == {
        "server_id": 8,
        "disk_space": None,
        "success": False,
    }

    monkeypatch.setattr(
        system_info_module.disk_space_service,
        "get_many_disk_space",
        AsyncMock(return_value=[{"used_gb": 2}, None]),
    )
    servers = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    assert await service.get_all_servers_disk_space(servers, force_refresh=True) == {
        1: {"used_gb": 2},
        2: None,
    }


@pytest.mark.asyncio
async def test_ai_retention_cleanup_clamps_retention_and_deletes_old_rows(monkeypatch):
    service = retention_module.AIRetentionService()
    old = SimpleNamespace(updated_at=None)
    new = SimpleNamespace(updated_at=None)
    db = _Db(execute_results=[_ScalarResult(rows=[old, new])])
    monkeypatch.setattr(retention_module, "async_session_maker", lambda: _DbContext(db))
    monkeypatch.setattr(
        retention_module,
        "cleanup_expired_ai_runs",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(
        retention_module.AISystemSettings,
        "get_or_create",
        AsyncMock(return_value=SimpleNamespace(history_retention_days=999)),
    )

    assert await service.cleanup_once() == 2
    assert db.deleted == [old, new]
    assert db.commits == 1
    assert await service.cleanup_background_tasks_once() == 2


@pytest.mark.asyncio
async def test_ai_retention_start_stop_and_loop_cleanup(monkeypatch):
    service = retention_module.AIRetentionService()
    monkeypatch.setattr(retention_module, "interrupt_active_ai_runs", AsyncMock(return_value=1))
    diagnostics = AsyncMock(return_value=1)
    monkeypatch.setattr(
        "services.plugin_diagnostic_service.interrupt_active_plugin_diagnostics", diagnostics
    )
    cleanup = AsyncMock(return_value=0)
    monkeypatch.setattr(service, "cleanup_once", cleanup)

    async def stop_sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(retention_module.asyncio, "sleep", stop_sleep)
    await service.start()
    assert cleanup.await_count == 1
    await service.stop()
    assert service._task is None
    await service.stop()


@pytest.mark.asyncio
async def test_legacy_user_settings_routes_cover_defaults_updates_and_reset(monkeypatch):
    user = SimpleNamespace(id=11)
    assert "steamcmd_mirrors" in await user_settings.get_mirror_presets()

    class _Column:
        def __eq__(self, _value):
            return self

    class _Query:
        def filter(self, _condition):
            return self

    class _UserSettings:
        user_id = _Column()

        def __init__(self, **values):
            self.__dict__.update(values)

    monkeypatch.setattr(user_settings, "UserSettings", _UserSettings)
    monkeypatch.setattr(user_settings, "select", lambda *_args: _Query())
    monkeypatch.setattr(user_settings, "col", lambda *_args: _Column())

    existing = SimpleNamespace(
        user_id=11,
        steamcmd_mirror_url="old-steam",
        github_api_mirror_url="old-github",
    )
    db = _Db(execute_results=[_ScalarResult(None), _ScalarResult(existing)])
    result = await user_settings.get_user_settings(user, db)
    assert result.steamcmd_mirror_url is None

    db = _Db(execute_results=[_ScalarResult(existing)])
    updated = await user_settings.update_user_settings(
        UserSettingsUpdate(steamcmd_mirror_url="new-steam"), user, db
    )
    assert updated.steamcmd_mirror_url == "new-steam"
    assert existing.github_api_mirror_url == "old-github"

    db = _Db(execute_results=[_ScalarResult(None)])
    created = await user_settings.update_user_settings(
        UserSettingsUpdate(github_api_mirror_url="new-github"), user, db
    )
    assert created.github_api_mirror_url == "new-github"
    assert len(db.added) == 1

    db = _Db(execute_results=[_ScalarResult(existing)])
    assert (await user_settings.reset_user_settings(user, db))["success"] is True
    assert db.deleted == [existing]
    db = _Db(execute_results=[_ScalarResult(None)])
    assert (await user_settings.reset_user_settings(user, db))["success"] is True


@pytest.mark.asyncio
async def test_legacy_settings_routes_cover_get_update_and_not_found():
    class _Column:
        def in_(self, _value):
            return self

        def __eq__(self, _value):
            return self

    class _Query:
        def filter(self, _condition):
            return self

    monkeypatch = pytest.MonkeyPatch()

    class _GlobalSettings:
        setting_key = _Column()

        def __init__(self, **values):
            self.__dict__.update(values)

    monkeypatch.setattr(legacy_settings, "select", lambda *_args: _Query())
    monkeypatch.setattr(legacy_settings, "col", lambda *_args: _Column())
    monkeypatch.setattr(legacy_settings, "GlobalSettings", _GlobalSettings)
    rows = [
        SimpleNamespace(setting_key="auto_restart_max_restarts", setting_value="7"),
        SimpleNamespace(setting_key="auto_restart_time_window_minutes", setting_value="12"),
    ]
    db = _Db(execute_results=[_ScalarResult(rows=rows)])
    result = await legacy_settings.get_auto_restart_settings(db)
    assert result == AutoRestartSettings(
        max_restarts=7, time_window_minutes=12, default_interval=60
    )

    existing = SimpleNamespace(setting_key="auto_restart_max_restarts", setting_value="1")
    db = _Db(
        execute_results=[
            _ScalarResult(existing),
            _ScalarResult(None),
            _ScalarResult(None),
        ]
    )
    admin = SimpleNamespace(id=1, is_admin=True)
    updated = await legacy_settings.update_auto_restart_settings(
        AutoRestartSettings(max_restarts=6, time_window_minutes=9, default_interval=30),
        db,
        admin,
    )
    assert updated.max_restarts == 6
    assert existing.setting_value == "6"
    assert len(db.added) == 2

    row = SimpleNamespace(id=1, setting_key="x", setting_value="y", description=None)
    db = _Db(execute_results=[_ScalarResult(rows=[row])])
    assert await legacy_settings.get_all_settings(db, admin) == [row]
    db = _Db(execute_results=[_ScalarResult(row)])
    assert await legacy_settings.get_setting("x", db, admin) is row
    db = _Db(execute_results=[_ScalarResult(None)])
    with pytest.raises(HTTPException, match="not found"):
        await legacy_settings.get_setting("x", db, admin)
    db = _Db(execute_results=[_ScalarResult(row)])
    assert (
        await legacy_settings.update_setting(
            "x", GlobalSettingsUpdate(setting_value="z"), db, admin
        )
        is row
    )
    assert row.setting_value == "z"
    db = _Db(execute_results=[_ScalarResult(None)])
    with pytest.raises(HTTPException, match="not found"):
        await legacy_settings.update_setting(
            "x", GlobalSettingsUpdate(setting_value="z"), db, admin
        )
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_legacy_plugin_catalog_routes_cover_list_get_create_and_errors(monkeypatch):
    assert len(await legacy_plugins.get_plugin_categories()) == len(PluginCategory)
    plugin = _legacy_plugin()
    monkeypatch.setattr(legacy_plugins.Plugin, "get_all_enabled", AsyncMock(return_value=[plugin]))
    monkeypatch.setattr(legacy_plugins.Plugin, "count_by_category", AsyncMock(return_value=1))
    db = _Db()
    listed = await legacy_plugins.list_plugins(category=None, page=1, page_size=20, db=db)
    assert listed.total_pages == 1
    monkeypatch.setattr(legacy_plugins.Plugin, "get_by_category", AsyncMock(return_value=[plugin]))
    categorized = await legacy_plugins.list_plugins(category="utility", page=2, page_size=10, db=db)
    assert categorized.page == 2

    db = _Db(get_results=[plugin])
    assert (await legacy_plugins.get_plugin(7, db)).id == 7
    with pytest.raises(HTTPException, match="not found"):
        await legacy_plugins.get_plugin(7, _Db())

    member = SimpleNamespace(is_admin=False)
    with pytest.raises(HTTPException, match="administrators"):
        await legacy_plugins.create_plugin(PluginCreate(name="a", display_name="A"), member, db)
    with pytest.raises(HTTPException, match="Invalid category"):
        invalid = PluginCreate(name="a", display_name="A")
        invalid.category = "bad"
        await legacy_plugins.create_plugin(invalid, SimpleNamespace(is_admin=True), db)
    db = _Db()
    created = await legacy_plugins.create_plugin(
        PluginCreate(name="new", display_name="New", category=PluginCategory.UTILITY),
        SimpleNamespace(is_admin=True),
        db,
    )
    assert created.name == "new"
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_legacy_plugin_upload_install_and_uninstall_are_isolated(monkeypatch, tmp_path):
    admin = SimpleNamespace(is_admin=True, id=11)
    member = SimpleNamespace(is_admin=False, id=11)
    db = _Db()
    upload = UploadFile(filename="demo.tar.gz", file=io.BytesIO(b"archive"))
    monkeypatch.setattr(legacy_plugins.os, "getcwd", lambda: str(tmp_path))
    result = await legacy_plugins.upload_plugin(
        upload,
        "demo",
        "Demo",
        "desc",
        "utility",
        "1$2",
        author=None,
        homepage=None,
        dependencies=None,
        install_path="addons/plugins",
        config_required=False,
        current_user=admin,
        db=db,
    )
    assert result.name == "demo"
    assert (tmp_path / "static/uploads/plugins/demo_12.tar.gz").exists()

    with pytest.raises(HTTPException, match=r"\.tar\.gz"):
        await legacy_plugins.upload_plugin(
            UploadFile(filename="demo.zip", file=io.BytesIO(b"x")),
            "demo",
            "Demo",
            "desc",
            "utility",
            "1",
            author=None,
            homepage=None,
            dependencies=None,
            install_path="addons/plugins",
            config_required=False,
            current_user=admin,
            db=db,
        )
    with pytest.raises(HTTPException, match="alphanumeric"):
        await legacy_plugins.upload_plugin(
            UploadFile(filename="demo.tar.gz", file=io.BytesIO(b"x")),
            "../demo",
            "Demo",
            "desc",
            "utility",
            "1",
            author=None,
            homepage=None,
            dependencies=None,
            install_path="addons/plugins",
            config_required=False,
            current_user=admin,
            db=db,
        )
    with pytest.raises(HTTPException, match="administrators"):
        await legacy_plugins.upload_plugin(
            upload,
            "demo",
            "Demo",
            "desc",
            "utility",
            "1",
            author=None,
            homepage=None,
            dependencies=None,
            install_path="addons/plugins",
            config_required=False,
            current_user=member,
            db=db,
        )

    server = _legacy_server()
    installed = SimpleNamespace(id=4, server_id=3, plugin_id=7, installed_version="1.2.3")
    monkeypatch.setattr(legacy_plugins.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    monkeypatch.setattr(
        legacy_plugins.InstalledPlugin, "get_by_server", AsyncMock(return_value=[installed])
    )
    db = _Db(get_results=[_legacy_plugin()])
    installed_result = await legacy_plugins.get_installed_plugins(3, member, db)
    assert installed_result[0].plugin.name == "demo"

    monkeypatch.setattr(
        legacy_plugins.InstalledPlugin, "get_by_server_and_plugin", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(legacy_plugins, "_install_plugin_to_server", AsyncMock(return_value=True))
    plugin = _legacy_plugin(dependencies=json.dumps([9]))
    db = _Db(get_results=[plugin, _legacy_plugin(id=9, name="dep")])
    response = await legacy_plugins.install_plugin(
        3,
        PluginInstallRequest(plugin_id=7),
        member,
        db,
        server,
    )
    assert response.body and b"installed successfully" in response.body

    monkeypatch.setattr(
        legacy_plugins,
        "SSHManager",
        lambda: SimpleNamespace(
            connect=AsyncMock(return_value=(True, "ok")),
            execute_command=AsyncMock(return_value=(True, "", "")),
            disconnect=AsyncMock(),
        ),
    )
    monkeypatch.setattr(legacy_plugins, "_install_plugin_to_server", AsyncMock(return_value=False))
    db = _Db(get_results=[plugin, _legacy_plugin(id=9, name="dep")])
    with pytest.raises(HTTPException, match="Failed to install"):
        await legacy_plugins.install_plugin(
            3, PluginInstallRequest(plugin_id=7), member, db, server
        )
