"""用 fake SSH/数据库覆盖 MapChooser 路由的事务与冲突分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import map_management as maps
from services.maintenance_lock import OperationBusyError

REVISION = "a" * 64


class _Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def scalar_one_or_none(self):
        return self.scalar


class _Db:
    def __init__(self, results=()):
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _query):
        return self.results.pop(0) if self.results else _Result()

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None


class _Lock:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return None


class _Ssh:
    def __init__(self):
        self.disconnect = AsyncMock()
        self.execute_command = AsyncMock(return_value=(True, "", ""))
        self.read_file = AsyncMock(return_value=(True, "maps", ""))
        self.write_file = AsyncMock(return_value=(True, ""))


def _server(**overrides):
    values = dict(
        id=3,
        user_id=7,
        game_directory="/srv/cs2",
        map_pool_sync_url="https://maps.invalid/pool",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _prereqs(**overrides):
    values = dict(
        counterstrikesharp_installed=True,
        mapchooser_installed=True,
        maps_file_exists=True,
        plugin_config_file_exists=True,
        ready=True,
    )
    values.update(overrides)
    return values


def _patch_common(monkeypatch, ssh, server=None):
    server = server or _server()
    monkeypatch.setattr(maps, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(maps, "_connect", AsyncMock(return_value=ssh))
    monkeypatch.setattr(maps, "_inspect_prerequisites", AsyncMock(return_value=_prereqs()))
    monkeypatch.setattr(maps.maintenance_lock_service, "get", lambda *_args, **_kwargs: _Lock())
    return server


@pytest.mark.asyncio
async def test_map_helpers_and_prerequisite_routes_cover_failures(monkeypatch):
    server = _server()
    assert maps._remote_paths(server)["maps"].endswith("maps.txt")
    ssh = _Ssh()
    ssh.execute_command.return_value = (True, "counterstrikesharp=1\nmapchooser=0\nmaps_file=1\nconfig_file=0", "")
    result = await maps._inspect_prerequisites(ssh, server)
    assert result["counterstrikesharp_installed"] and not result["ready"]
    with pytest.raises(HTTPException) as exc_info:
        maps._require_prerequisites(result)
    assert exc_info.value.status_code == 412
    with pytest.raises(HTTPException) as exc_info:
        maps._require_prerequisites({"counterstrikesharp_installed": False, "mapchooser_installed": False})
    assert exc_info.value.status_code == 412
    ssh.execute_command.return_value = (False, "", "permission denied")
    with pytest.raises(HTTPException) as exc_info:
        await maps._inspect_prerequisites(ssh, server)
    assert exc_info.value.status_code == 502

    ssh = _Ssh()
    assert await maps._read_maps_config(ssh, server, False) == (maps.DEFAULT_MAPS_CONFIG, False)
    assert await maps._read_plugin_config(ssh, server, False) == (maps.DEFAULT_PLUGIN_CONFIG_CONTENT, False)
    ssh.read_file.return_value = (False, "", "unreadable")
    with pytest.raises(HTTPException):
        await maps._read_maps_config(ssh, server, True)
    with pytest.raises(HTTPException):
        await maps._read_plugin_config(ssh, server, True)
    assert maps._map_count({"maps": []}) == 0
    assert maps._map_count({"maps": "bad"}) == 0
    payload = maps._config_payload("bad", maps_file_exists=True, prerequisites={})
    assert payload["config_error"]
    plugin_payload = maps._plugin_config_payload("bad", config_file_exists=True, prerequisites={})
    assert plugin_payload["config_error"]

    ssh.execute_command.return_value = (False, "", "mkdir failed")
    with pytest.raises(HTTPException):
        await maps._replace_remote_config(ssh, server, "/srv/cs2/maps.txt", "x", "maps.txt")
    ssh.execute_command.side_effect = [(True, "", ""), (False, "", "move failed"), (True, "", "")]
    with pytest.raises(HTTPException, match="replace"):
        await maps._replace_remote_config(ssh, server, "/srv/cs2/maps.txt", "x", "maps.txt")


@pytest.mark.asyncio
async def test_map_status_custom_sync_and_uninstall_routes(monkeypatch):
    ssh = _Ssh()
    server = _patch_common(monkeypatch, ssh)
    db = _Db([_Result(rows=[])])
    assert (await maps.get_map_management_status(3, db, object()))["ready"] is True
    assert await maps.get_custom_map_sync(3, db, object()) == {
        "url": server.map_pool_sync_url,
        "enabled": False,
        "interval_seconds": maps.MAP_POOL_SYNC_MIN_INTERVAL_SECONDS,
        "last_run": None,
        "next_run": None,
        "last_status": None,
        "last_error": None,
        "run_count": 0,
    }

    task = SimpleNamespace(
        schedule_value="bad", enabled=True, last_run=None, next_run=None,
        last_status="failed", last_error="old", run_count=2,
    )
    db = _Db([_Result(rows=[task, SimpleNamespace(enabled=True, next_run="old")])])
    monkeypatch.setattr(maps, "validate_remote_map_url", AsyncMock(return_value="https://maps.invalid/normalized"))
    updated = await maps.update_custom_map_sync(
        3, maps.CustomMapSyncUpdateRequest(url="https://maps.invalid/pool", interval_seconds=300, enabled=True), db, object()
    )
    assert updated["enabled"] is True and task.enabled is True
    assert db.commits == 1

    db = _Db([_Result(rows=[])])
    server.map_pool_sync_url = None
    with pytest.raises(HTTPException, match="URL"):
        await maps.run_custom_map_sync(3, maps.CustomMapSyncRunRequest(expected_revision=REVISION), db, object())
    server.map_pool_sync_url = "https://maps.invalid/pool"
    monkeypatch.setattr(maps, "_read_maps_config", AsyncMock(return_value=("maps", True)))
    monkeypatch.setattr(maps, "content_revision", lambda _content: REVISION)
    monkeypatch.setattr(maps, "parse_maps_config", lambda _content: SimpleNamespace(maps=[]))
    monkeypatch.setattr(maps, "fetch_remote_map_pool", AsyncMock(return_value="updated maps"))
    monkeypatch.setattr(maps, "_replace_maps_config", AsyncMock())
    monkeypatch.setattr(maps, "_record_map_sync_result", AsyncMock())
    monkeypatch.setattr(maps, "_config_payload", lambda *args, **kwargs: {"maps": []})
    result = await maps.run_custom_map_sync(3, maps.CustomMapSyncRunRequest(expected_revision=REVISION), db, object())
    assert result["message"]
    monkeypatch.setattr(maps, "fetch_remote_map_pool", AsyncMock(side_effect=maps.RemoteMapPoolError("remote")))
    with pytest.raises(HTTPException) as exc_info:
        await maps.run_custom_map_sync(3, maps.CustomMapSyncRunRequest(expected_revision=REVISION), db, object())
    assert exc_info.value.status_code == 502

    monkeypatch.setattr(maps, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(maps, "_get_map_sync_tasks", AsyncMock(return_value=[]))
    monkeypatch.setattr(maps, "_connect", AsyncMock(return_value=ssh))
    tracked = SimpleNamespace(auto_update_enabled=True, last_status="installed", last_error="x")
    db = _Db([_Result(rows=[tracked])])
    result = await maps.uninstall_mapchooser_plugin(
        3, maps.MapChooserUninstallRequest(confirmation=maps.MAPCHOOSER_UNINSTALL_CONFIRMATION), db, object()
    )
    assert result["success"] and tracked.last_status == "uninstalled"
    with pytest.raises(HTTPException, match="confirmation"):
        await maps.uninstall_mapchooser_plugin(3, maps.MapChooserUninstallRequest(confirmation="no"), db, object())


@pytest.mark.asyncio
async def test_map_config_preset_and_map_mutation_routes(monkeypatch):
    ssh = _Ssh()
    server = _patch_common(monkeypatch, ssh)
    db = _Db()
    monkeypatch.setattr(maps, "_read_maps_config", AsyncMock(return_value=("maps", True)))
    monkeypatch.setattr(maps, "_read_plugin_config", AsyncMock(return_value=("config", True)))
    monkeypatch.setattr(maps, "_replace_maps_config", AsyncMock())
    monkeypatch.setattr(maps, "_replace_plugin_config", AsyncMock())
    monkeypatch.setattr(maps, "_plugin_config_payload", lambda *args, **kwargs: {"fields": []})
    monkeypatch.setattr(maps, "_config_payload", lambda *args, **kwargs: {"maps": []})
    monkeypatch.setattr(maps, "update_plugin_config", lambda content, values, **_kwargs: content + " updated")
    monkeypatch.setattr(maps, "parse_maps_config", lambda _content: SimpleNamespace(maps=[]))
    monkeypatch.setattr(maps, "content_revision", lambda _content: REVISION)

    assert (await maps.get_plugin_config(3, db, object()))["fields"] == []
    assert (await maps.get_maps_config(3, db, object()))["maps"] == []
    result = await maps.update_mapchooser_plugin_config(
        3, maps.PluginConfigUpdateRequest(values={"VoteDuration": 10}, expected_revision=REVISION), db, object()
    )
    assert result["message"]
    result = await maps.update_maps_config(
        3, maps.MapConfigUpdateRequest(content="maps", expected_revision=REVISION), db, object()
    )
    assert result["message"]

    monkeypatch.setattr(maps, "_official_maps_config", AsyncMock(return_value="official"))
    result = await maps.apply_map_preset(
        3, maps.MapPresetApplyRequest(preset="official", expected_revision=REVISION), db, object()
    )
    assert result["preset"] == "official"
    monkeypatch.setattr(maps, "_remote_maps_config", AsyncMock(return_value="kz maps"))
    result = await maps.apply_map_preset(
        3, maps.MapPresetApplyRequest(preset="kz", expected_revision=REVISION, plugin_config_expected_revision=REVISION), db, object()
    )
    assert result["plugin_config"] is not None
    monkeypatch.setattr(maps, "normalize_workshop_id", lambda value: value)
    monkeypatch.setattr(maps, "validate_restricted_times", lambda value: value)
    monkeypatch.setattr(maps, "append_map_to_config", lambda content, **_kwargs: content + " added")
    monkeypatch.setattr(maps, "_fetch_workshop_title", AsyncMock(return_value=None))
    with pytest.raises(HTTPException, match="title"):
        await maps.add_map(3, maps.MapAddRequest(workshop_id="123"), db, object())
    result = await maps.add_map(3, maps.MapAddRequest(workshop_id="123", name="Map"), db, object())
    assert result["added_map"]["name"] == "Map"

    monkeypatch.setattr(maps, "set_map_enabled", lambda content, **_kwargs: content + " toggled")
    result = await maps.update_map_enabled(
        3, maps.MapEnabledUpdateRequest(name="Map", workshop_id="123", expected_revision=REVISION, enabled=False), db, object()
    )
    assert "Disabled" in result["message"]
    monkeypatch.setattr(maps, "remove_map_from_config", lambda content, **_kwargs: content + " removed")
    result = await maps.delete_map(
        3, maps.MapIdentityRequest(name="Map", workshop_id="123", expected_revision=REVISION), db, object()
    )
    assert result["message"]


@pytest.mark.asyncio
async def test_map_route_conflict_and_lock_errors_are_translated(monkeypatch):
    ssh = _Ssh()
    server = _patch_common(monkeypatch, ssh)
    db = _Db()
    monkeypatch.setattr(maps.maintenance_lock_service, "get", lambda *_args, **_kwargs: _Lock())
    monkeypatch.setattr(maps, "_read_maps_config", AsyncMock(return_value=("maps", True)))
    monkeypatch.setattr(maps, "content_revision", lambda _content: REVISION)
    monkeypatch.setattr(maps, "parse_maps_config", lambda _content: SimpleNamespace(maps=[]))
    with pytest.raises(HTTPException) as exc_info:
        await maps.update_maps_config(
            3, maps.MapConfigUpdateRequest(content="maps", expected_revision="b" * 64), db, object()
        )
    assert exc_info.value.status_code == 409
    monkeypatch.setattr(maps.maintenance_lock_service, "get", lambda *_args, **_kwargs: _Busy())
    with pytest.raises(OperationBusyError):
        await maps.update_mapchooser_plugin_config(
            3, maps.PluginConfigUpdateRequest(values={}), db, object()
        )


class _Busy:
    async def __aenter__(self):
        raise OperationBusyError("busy")

    async def __aexit__(self, *_args):
        return None
