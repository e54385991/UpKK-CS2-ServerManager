"""Route-level behavior for plugin configuration source and save APIs."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException

from api.routes import plugin_configs
from modules import DEFAULT_PLUGIN_CONFIG_SOURCE_PATH, DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS
from services.plugin_config_service import path_hash


class _LockContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _LockService:
    def get(self, *_args, **_kwargs):
        return _LockContext()


def test_default_configuration_sources_cover_css_configs_and_game_cfg():
    assert DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS == (
        "cs2/game/csgo/addons/counterstrikesharp/configs",
        "cs2/game/csgo/cfg",
    )
    assert DEFAULT_PLUGIN_CONFIG_SOURCE_PATH == DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS[0]


@pytest.mark.asyncio
async def test_restore_default_source_restores_both_directories(monkeypatch):
    missing = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[missing, missing]),
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    monkeypatch.setattr(
        plugin_configs,
        "get_server_with_permission",
        AsyncMock(return_value=SimpleNamespace(game_directory="/home/cs2")),
    )

    response = await plugin_configs.restore_default_source(
        server_id=7,
        db=db,
        current_user=SimpleNamespace(),
    )

    restored = [call.args[0] for call in db.add.call_args_list]
    assert [source.relative_path for source in restored] == list(DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS)
    assert [source.path_hash for source in restored] == [
        path_hash(source_path) for source_path in DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS
    ]
    assert all(source.is_default and source.is_enabled for source in restored)
    assert [source["path"] for source in response["sources"]] == list(
        DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS
    )
    db.commit.assert_awaited_once()
    assert db.refresh.await_count == 2


@pytest.mark.asyncio
async def test_cross_server_source_id_is_not_returned():
    result = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    with pytest.raises(HTTPException) as exc:
        await plugin_configs._source_for_server(db, server_id=10, source_id=99)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("connect_result", [(False, "denied"), RuntimeError("broken")])
async def test_connect_releases_manager_when_connection_does_not_succeed(connect_result):
    manager = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock())
    if isinstance(connect_result, Exception):
        manager.connect.side_effect = connect_result
        expected_exception = RuntimeError
    else:
        manager.connect.return_value = connect_result
        expected_exception = HTTPException

    with pytest.raises(expected_exception):
        await plugin_configs._connect(SimpleNamespace(), manager)

    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_removing_default_source_soft_disables_it(monkeypatch):
    source = SimpleNamespace(is_default=True, is_enabled=True)
    db = SimpleNamespace(add=Mock(), delete=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(
        plugin_configs, "get_server_with_permission", AsyncMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(plugin_configs, "_source_for_server", AsyncMock(return_value=source))

    response = await plugin_configs.delete_source(
        server_id=1, source_id=2, db=db, current_user=SimpleNamespace()
    )

    assert response == {"success": True}
    assert source.is_enabled is False
    db.add.assert_called_once_with(source)
    db.delete.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_adding_source_commits_and_returns_persisted_id(monkeypatch):
    server = SimpleNamespace(id=7, game_directory="/home/cs2")
    manager = SimpleNamespace(disconnect=AsyncMock())
    existing_result = SimpleNamespace(scalar_one_or_none=lambda: None)

    async def assign_database_id(source):
        source.id = 42

    db = SimpleNamespace(
        execute=AsyncMock(return_value=existing_result),
        add=Mock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        refresh=AsyncMock(side_effect=assign_database_id),
    )
    monkeypatch.setattr(
        plugin_configs, "get_server_with_permission", AsyncMock(return_value=server)
    )
    monkeypatch.setattr(plugin_configs, "_connect", AsyncMock(return_value=manager))
    monkeypatch.setattr(plugin_configs, "inspect_source", AsyncMock(return_value="directory"))

    response = await plugin_configs.create_source(
        server_id=server.id,
        request=plugin_configs.SourceCreateRequest(path="configs/custom"),
        db=db,
        current_user=SimpleNamespace(),
    )

    assert response["id"] == 42
    assert response["path"] == "configs/custom"
    assert response["persisted"] is True
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once()
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_scan_route_streams_files_and_disconnects(monkeypatch):
    server = SimpleNamespace(game_directory="/home/cs2")
    source = SimpleNamespace(relative_path="configs", source_type="directory")
    manager = SimpleNamespace(disconnect=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())

    async def stream_scan(*_args):
        yield {"type": "progress", "directory": ".", "count": 0}
        yield {"type": "file", "file": {"tree_path": "plugin.cfg"}}
        yield {"type": "complete", "count": 1, "truncated": False}

    monkeypatch.setattr(
        plugin_configs, "get_server_with_permission", AsyncMock(return_value=server)
    )
    monkeypatch.setattr(plugin_configs, "_source_for_server", AsyncMock(return_value=source))

    async def connect(*_args):
        db.commit.assert_awaited_once()
        return manager

    monkeypatch.setattr(plugin_configs, "_connect", connect)
    monkeypatch.setattr(plugin_configs, "iter_source_scan", stream_scan)

    response = await plugin_configs.load_source_files(
        server_id=1,
        source_id=2,
        db=db,
        current_user=SimpleNamespace(),
    )
    chunks = [chunk async for chunk in response.body_iterator]
    events = [json.loads(line) for line in "".join(chunks).splitlines()]

    assert [event["type"] for event in events] == ["start", "progress", "file", "complete"]
    assert response.media_type == "application/x-ndjson"
    assert response.headers["x-accel-buffering"] == "no"
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_scan_route_documents_and_preserves_the_ndjson_event_contract(monkeypatch):
    server = SimpleNamespace(game_directory="/home/cs2")
    source = SimpleNamespace(relative_path="configs", source_type="directory")
    manager = SimpleNamespace(disconnect=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    streamed_events = [
        {"type": "progress", "directory": ".", "count": 0},
        {
            "type": "file",
            "file": {
                "name": "plugin.cfg",
                "path": "configs/plugin.cfg",
                "tree_path": "plugin.cfg",
                "size": 128,
                "modified": 1_700_000_000.5,
                "format": "keyvalues",
                "too_large": False,
            },
        },
        {"type": "complete", "count": 1, "truncated": False},
    ]

    async def stream_scan(*_args):
        for event in streamed_events:
            yield event

    monkeypatch.setattr(
        plugin_configs,
        "get_server_with_permission",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(
        plugin_configs,
        "_source_for_server",
        AsyncMock(return_value=source),
    )

    async def connect(*_args):
        db.commit.assert_awaited_once()
        return manager

    monkeypatch.setattr(plugin_configs, "_connect", connect)
    monkeypatch.setattr(plugin_configs, "iter_source_scan", stream_scan)

    response = await plugin_configs.load_source_files(
        server_id=1,
        source_id=2,
        db=db,
        current_user=SimpleNamespace(),
    )
    raw_body = "".join([chunk async for chunk in response.body_iterator])
    events = [json.loads(line) for line in raw_body.splitlines()]

    assert response.media_type == "application/x-ndjson"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert events == [{"type": "start"}, *streamed_events]
    assert raw_body.endswith("\n")
    manager.disconnect.assert_awaited_once()

    app = FastAPI()
    app.include_router(plugin_configs.router)
    operation = app.openapi()["paths"][
        "/servers/{server_id}/plugin-configs/sources/{source_id}/scan"
    ]["post"]
    success_content = operation["responses"]["200"]["content"]

    assert set(success_content) == {"application/x-ndjson"}
    stream_schema = success_content["application/x-ndjson"]["schema"]
    assert stream_schema["type"] == "string"
    assert stream_schema["format"] == "ndjson"
    line_schema = stream_schema["x-ndjson-line-schema"]
    assert {event_schema["title"] for event_schema in line_schema["oneOf"]} == {
        "PluginConfigScanStartEvent",
        "PluginConfigScanProgressEvent",
        "PluginConfigScanFileEvent",
        "PluginConfigScanCompleteEvent",
        "PluginConfigScanErrorEvent",
    }
    file_schema = next(
        item for item in line_schema["oneOf"] if item["title"] == "PluginConfigScanFileEvent"
    )
    assert set(file_schema["properties"]["file"]["properties"]) == {
        "name",
        "path",
        "tree_path",
        "size",
        "modified",
        "format",
        "too_large",
    }
    for status_code in ("401", "404"):
        assert operation["responses"][status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


@pytest.mark.asyncio
async def test_save_rejects_stale_revision_without_writing(monkeypatch):
    server = SimpleNamespace(game_directory="/home/cs2")
    source = SimpleNamespace(source_type="file", relative_path="cs2/game/csgo/cfg/plugin.cfg")
    manager = SimpleNamespace(disconnect=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    atomic_write = AsyncMock()
    monkeypatch.setattr(
        plugin_configs, "get_server_with_permission", AsyncMock(return_value=server)
    )
    monkeypatch.setattr(plugin_configs, "_source_for_server", AsyncMock(return_value=source))

    async def connect(*_args):
        db.commit.assert_awaited_once()
        return manager

    monkeypatch.setattr(plugin_configs, "_connect", connect)
    monkeypatch.setattr(plugin_configs, "read_text_file", AsyncMock(return_value="setting 1\n"))
    monkeypatch.setattr(plugin_configs, "atomic_write_text_file", atomic_write)
    monkeypatch.setattr(plugin_configs, "maintenance_lock_service", _LockService())
    request = plugin_configs.ConfigSaveRequest(
        path=source.relative_path,
        expected_revision="0" * 64,
        mode="visual",
        changes=[],
    )

    with pytest.raises(HTTPException) as exc:
        await plugin_configs.save_config_file(
            server_id=1,
            source_id=2,
            request=request,
            db=db,
            current_user=SimpleNamespace(),
        )

    assert exc.value.status_code == 409
    atomic_write.assert_not_awaited()
    manager.disconnect.assert_awaited_once()
