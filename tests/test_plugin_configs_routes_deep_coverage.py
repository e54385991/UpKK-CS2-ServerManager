"""覆盖插件配置路由的来源管理、扫描、读取和保存异常矩阵。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from api.routes import plugin_configs
from services.plugin_config_service import PluginConfigError, content_revision


class _Result:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.values))


class _Db:
    def __init__(self, results=()):
        self.results = list(results)
        self.added = []
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement):
        return self.results.pop(0) if self.results else _Result()

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, value):
        if getattr(value, "id", None) is None:
            value.id = 99


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _server():
    return SimpleNamespace(id=4, game_directory="/srv/cs2")


def _source(**overrides):
    values = dict(
        id=8,
        server_id=4,
        relative_path="cs2/game/csgo/cfg",
        source_type="directory",
        is_default=False,
        is_enabled=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_server(monkeypatch, server=None):
    monkeypatch.setattr(
        plugin_configs, "get_server_with_permission", AsyncMock(return_value=server or _server())
    )


@pytest.mark.asyncio
async def test_sources_list_delete_restore_existing_and_create_edge_paths(monkeypatch):
    server = _server()
    source = _source()
    _patch_server(monkeypatch, server)
    listed = await plugin_configs.list_sources(
        4,
        _Db([_Result(values=[source])]),
        SimpleNamespace(),
    )
    assert listed["game_directory"] == "/srv/cs2"
    assert listed["sources"][0]["absolute_path"].endswith("cfg")

    db = _Db([_Result(source)])
    result = await plugin_configs.delete_source(4, 8, db, SimpleNamespace())
    assert result == {"success": True} and db.deleted == [source]

    existing_disabled = _source(id=9, is_enabled=False)
    manager = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(plugin_configs, "_connect", AsyncMock(return_value=manager))
    monkeypatch.setattr(plugin_configs, "inspect_source", AsyncMock(return_value="file"))
    db = _Db([_Result(existing_disabled)])
    response = await plugin_configs.create_source(
        4, plugin_configs.SourceCreateRequest(path="custom/plugin.cfg"), db, SimpleNamespace()
    )
    assert response["id"] == 9 and existing_disabled.is_enabled
    assert existing_disabled.source_type == "file"

    db = _Db([_Result(_source(is_enabled=True))])
    with pytest.raises(HTTPException, match="already exists"):
        await plugin_configs.create_source(
            4, plugin_configs.SourceCreateRequest(path="custom/plugin.cfg"), db, SimpleNamespace()
        )

    monkeypatch.setattr(
        plugin_configs, "_connect", AsyncMock(side_effect=HTTPException(502, "offline"))
    )
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.create_source(
            4, plugin_configs.SourceCreateRequest(path="custom/other.cfg"), _Db(), SimpleNamespace()
        )
    assert exc_info.value.status_code == 502
    monkeypatch.setattr(plugin_configs, "_connect", AsyncMock(return_value=manager))
    monkeypatch.setattr(
        plugin_configs, "inspect_source", AsyncMock(side_effect=asyncssh.Error(1, "remote"))
    )
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.create_source(
            4,
            plugin_configs.SourceCreateRequest(path="custom/remote.cfg"),
            _Db(),
            SimpleNamespace(),
        )
    assert exc_info.value.status_code == 502
    manager.disconnect.assert_awaited()

    monkeypatch.setattr(plugin_configs, "inspect_source", AsyncMock(return_value="file"))
    db = _Db([_Result()])
    db.commit = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate")))
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.create_source(
            4, plugin_configs.SourceCreateRequest(path="custom/new.cfg"), db, SimpleNamespace()
        )
    assert exc_info.value.status_code == 409 and db.rollbacks == 1

    monkeypatch.setattr(plugin_configs, "_connect", AsyncMock(return_value=manager))
    monkeypatch.setattr(
        plugin_configs, "inspect_source", AsyncMock(side_effect=PluginConfigError("bad"))
    )
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.create_source(
            4, plugin_configs.SourceCreateRequest(path="custom/bad.cfg"), _Db(), SimpleNamespace()
        )
    assert exc_info.value.status_code == 422

    restore_existing = _source(id=10, is_default=False, is_enabled=False)
    db = _Db([_Result(restore_existing), _Result(None)])
    response = await plugin_configs.restore_default_source(4, db, SimpleNamespace())
    assert len(response["sources"]) == 2
    assert all(item.is_default and item.is_enabled for item in db.added)


@pytest.mark.asyncio
async def test_browse_scan_and_file_routes_cover_remote_errors(monkeypatch):
    _patch_server(monkeypatch)
    source = _source()
    manager = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(plugin_configs, "_connect", AsyncMock(return_value=manager))
    monkeypatch.setattr(plugin_configs, "browse_directory", AsyncMock(return_value=[{"name": "a"}]))
    response = await plugin_configs.browse_source_path(
        4, path="cs2/game/csgo/cfg", db=_Db(), current_user=SimpleNamespace()
    )
    assert response["items"] == [{"name": "a"}]

    monkeypatch.setattr(
        plugin_configs, "browse_directory", AsyncMock(side_effect=asyncssh.Error(1, "bad"))
    )
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.browse_source_path(
            4, path="cs2/game/csgo/cfg", db=_Db(), current_user=SimpleNamespace()
        )
    assert exc_info.value.status_code == 502
    monkeypatch.setattr(
        plugin_configs, "_connect", AsyncMock(side_effect=PluginConfigError("no ssh"))
    )
    with pytest.raises(PluginConfigError):
        await plugin_configs.browse_source_path(
            4, path="cs2/game/csgo/cfg", db=_Db(), current_user=SimpleNamespace()
        )

    monkeypatch.setattr(plugin_configs, "_connect", AsyncMock(return_value=manager))

    async def scan_events(*_args):
        yield {"type": "file", "file": {"name": "a.cfg"}}

    monkeypatch.setattr(plugin_configs, "iter_source_scan", scan_events)
    monkeypatch.setattr(plugin_configs, "_source_for_server", AsyncMock(return_value=source))
    response = await plugin_configs.load_source_files(4, 8, _Db(), SimpleNamespace())
    body = "".join([part async for part in response.body_iterator])
    assert [json.loads(line)["type"] for line in body.splitlines()] == ["start", "file"]
    manager.disconnect.assert_awaited()

    exceptions = [
        HTTPException(400, "bad request"),
        PluginConfigError("invalid config"),
        asyncssh.Error(1, "remote failure"),
        OSError("io failure"),
        RuntimeError("unexpected"),
    ]
    for exception in exceptions:

        async def failing_scan(*_args, error=exception):
            if error is not None:
                raise error
            yield {}

        monkeypatch.setattr(plugin_configs, "iter_source_scan", failing_scan)
        response = await plugin_configs.load_source_files(4, 8, _Db(), SimpleNamespace())
        lines = [json.loads(line) async for line in response.body_iterator]
        assert lines[0]["type"] == "start" and lines[-1]["type"] == "error"

    monkeypatch.setattr(plugin_configs, "read_text_file", AsyncMock(return_value="enabled 1\n"))
    file_response = await plugin_configs.get_config_file(
        4, 8, "cs2/game/csgo/cfg/a.cfg", _Db(), SimpleNamespace()
    )
    assert file_response["revision"] == content_revision("enabled 1\n")
    monkeypatch.setattr(
        plugin_configs, "read_text_file", AsyncMock(side_effect=asyncssh.Error(1, "read"))
    )
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.get_config_file(
            4, 8, "cs2/game/csgo/cfg/a.cfg", _Db(), SimpleNamespace()
        )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_config_file_validation_and_visual_raw_save_paths(monkeypatch):
    _patch_server(monkeypatch)
    directory = _source(relative_path="cs2/game/csgo/cfg")
    monkeypatch.setattr(plugin_configs, "_source_for_server", AsyncMock(return_value=directory))
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.get_config_file(4, 8, "../outside.cfg", _Db(), SimpleNamespace())
    assert exc_info.value.status_code == 422
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.get_config_file(
            4, 8, "cs2/game/csgo/cfg/file.dll", _Db(), SimpleNamespace()
        )
    assert exc_info.value.status_code == 415

    manager = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(plugin_configs, "_connect", AsyncMock(return_value=manager))
    monkeypatch.setattr(plugin_configs, "read_text_file", AsyncMock(return_value="enabled 1\n"))
    atomic = AsyncMock()
    monkeypatch.setattr(plugin_configs, "atomic_write_text_file", atomic)
    monkeypatch.setattr(
        plugin_configs, "maintenance_lock_service", SimpleNamespace(get=lambda *_a, **_k: _Lock())
    )
    current = "enabled 1\n"
    request = plugin_configs.ConfigSaveRequest(
        path="cs2/game/csgo/cfg/a.cfg",
        expected_revision=content_revision(current),
        mode="visual",
        changes=[{"id": "enabled", "value": 0}],
    )
    monkeypatch.setattr(plugin_configs, "apply_visual_changes", lambda *_args: "enabled 0\n")
    saved = await plugin_configs.save_config_file(4, 8, request, _Db(), SimpleNamespace())
    assert saved["message"].startswith("Configuration saved")
    atomic.assert_awaited_once()

    request = plugin_configs.ConfigSaveRequest(
        path="cs2/game/csgo/cfg/a.cfg",
        expected_revision=content_revision(current),
        mode="raw",
    )
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.save_config_file(4, 8, request, _Db(), SimpleNamespace())
    assert exc_info.value.status_code == 422
    request.path = "cs2/game/csgo/cfg/a.json"
    request.content = "{bad json}"
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.save_config_file(4, 8, request, _Db(), SimpleNamespace())
    assert exc_info.value.status_code == 422
    monkeypatch.setattr(
        plugin_configs, "atomic_write_text_file", AsyncMock(side_effect=OSError("write"))
    )
    request.path = "cs2/game/csgo/cfg/a.cfg"
    request.content = "enabled 2\n"
    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs.save_config_file(4, 8, request, _Db(), SimpleNamespace())
    assert exc_info.value.status_code == 502
    assert manager.disconnect.await_count >= 3
