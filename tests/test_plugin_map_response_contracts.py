"""Precise JSON contracts for map and plugin management routes."""

from __future__ import annotations

from fastapi import FastAPI

from api.routes import map_management, plugin_auto_update, plugin_configs


def _schema() -> dict:
    app = FastAPI()
    app.include_router(map_management.router)
    app.include_router(plugin_configs.router)
    app.include_router(plugin_auto_update.router)
    return app.openapi()


SUCCESS_MODELS = {
    ("get", "/servers/{server_id}/maps/status"): ("200", "MapPrerequisitesResponse"),
    ("get", "/servers/{server_id}/maps/custom-sync"): ("200", "CustomMapSyncResponse"),
    ("put", "/servers/{server_id}/maps/custom-sync"): (
        "200",
        "CustomMapSyncUpdateResponse",
    ),
    ("post", "/servers/{server_id}/maps/custom-sync/run"): (
        "200",
        "CustomMapSyncRunResponse",
    ),
    ("delete", "/servers/{server_id}/maps/plugin"): (
        "200",
        "MapChooserUninstallResponse",
    ),
    ("get", "/servers/{server_id}/maps/plugin-config"): ("200", "PluginConfigResponse"),
    ("put", "/servers/{server_id}/maps/plugin-config"): (
        "200",
        "PluginConfigUpdateResponse",
    ),
    ("get", "/servers/{server_id}/maps"): ("200", "MapsConfigResponse"),
    ("put", "/servers/{server_id}/maps"): ("200", "MapsConfigUpdateResponse"),
    ("post", "/servers/{server_id}/maps/preset"): ("200", "MapPresetResponse"),
    ("post", "/servers/{server_id}/maps"): ("200", "MapAddResponse"),
    ("patch", "/servers/{server_id}/maps"): ("200", "MapsConfigUpdateResponse"),
    ("delete", "/servers/{server_id}/maps"): ("200", "MapsConfigUpdateResponse"),
    ("get", "/servers/{server_id}/plugin-configs/sources"): (
        "200",
        "PluginConfigSourcesResponse",
    ),
    ("post", "/servers/{server_id}/plugin-configs/sources"): (
        "201",
        "PluginConfigSourceResponse",
    ),
    ("delete", "/servers/{server_id}/plugin-configs/sources/{source_id}"): (
        "200",
        "PluginConfigDeleteResponse",
    ),
    ("post", "/servers/{server_id}/plugin-configs/sources/restore-default"): (
        "200",
        "PluginConfigSourceRestoreResponse",
    ),
    ("get", "/servers/{server_id}/plugin-configs/browse"): (
        "200",
        "PluginConfigBrowseResponse",
    ),
    ("get", "/servers/{server_id}/plugin-configs/sources/{source_id}/file"): (
        "200",
        "PluginConfigFileResponse",
    ),
    ("put", "/servers/{server_id}/plugin-configs/sources/{source_id}/file"): (
        "200",
        "PluginConfigFileSaveResponse",
    ),
    ("get", "/api/servers/{server_id}/plugin-auto-update"): (
        "200",
        "PluginAutoUpdateResponse",
    ),
    ("put", "/api/servers/{server_id}/plugin-auto-update/settings"): (
        "200",
        "PluginAutoUpdateResponse",
    ),
    ("post", "/api/servers/{server_id}/plugin-auto-update/plugins"): (
        "201",
        "ManagedPluginResponse",
    ),
    ("patch", "/api/servers/{server_id}/plugin-auto-update/plugins/{plugin_id}"): (
        "200",
        "ManagedPluginResponse",
    ),
    ("delete", "/api/servers/{server_id}/plugin-auto-update/plugins/{plugin_id}"): (
        "200",
        "ActionResponse",
    ),
    ("post", "/api/servers/{server_id}/plugin-auto-update/run"): (
        "202",
        "ActionResponse",
    ),
    ("post", "/api/servers/{server_id}/plugin-auto-update/plugins/{plugin_id}/test-update"): (
        "202",
        "ActionResponse",
    ),
    ("get", "/api/servers/{server_id}/plugin-auto-update/status"): (
        "200",
        "PluginUpdateStatusResponse",
    ),
}


def test_json_routes_publish_named_success_models_and_explicit_statuses() -> None:
    paths = _schema()["paths"]

    for (method, path), (status_code, model_name) in SUCCESS_MODELS.items():
        operation = paths[path][method]
        response_schema = operation["responses"][status_code]["content"]["application/json"][
            "schema"
        ]
        assert response_schema == {"$ref": f"#/components/schemas/{model_name}"}
        assert operation["security"] == [{"OAuth2PasswordBearer": []}]


def test_lock_backed_routes_publish_coordination_and_conflict_errors() -> None:
    paths = _schema()["paths"]
    operations = (
        ("post", "/servers/{server_id}/maps/custom-sync/run"),
        ("delete", "/servers/{server_id}/maps/plugin"),
        ("put", "/servers/{server_id}/maps/plugin-config"),
        ("put", "/servers/{server_id}/maps"),
        ("post", "/servers/{server_id}/maps/preset"),
        ("post", "/servers/{server_id}/maps"),
        ("patch", "/servers/{server_id}/maps"),
        ("delete", "/servers/{server_id}/maps"),
        ("put", "/servers/{server_id}/plugin-configs/sources/{source_id}/file"),
        ("post", "/api/servers/{server_id}/plugin-auto-update/run"),
        (
            "post",
            "/api/servers/{server_id}/plugin-auto-update/plugins/{plugin_id}/test-update",
        ),
    )

    for method, path in operations:
        responses = paths[path][method]["responses"]
        for status_code in ("409", "503"):
            assert responses[status_code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }


def test_read_routes_publish_business_error_envelopes() -> None:
    paths = _schema()["paths"]
    operations = (
        ("get", "/servers/{server_id}/maps/status"),
        ("get", "/servers/{server_id}/maps"),
        ("get", "/servers/{server_id}/plugin-configs/sources"),
        ("get", "/servers/{server_id}/plugin-configs/browse"),
        ("get", "/servers/{server_id}/plugin-configs/sources/{source_id}/file"),
        ("get", "/api/servers/{server_id}/plugin-auto-update/status"),
    )

    for method, path in operations:
        assert paths[path][method]["responses"]["404"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_new_models_preserve_representative_existing_json_bodies() -> None:
    browse_body = {
        "path": "cs2/game/csgo/cfg",
        "items": [
            {"name": "linked.cfg", "type": "symlink", "selectable": False},
            {
                "name": "server.cfg",
                "path": "cs2/game/csgo/cfg/server.cfg",
                "type": "file",
                "selectable": True,
                "size": 42,
            },
        ],
    }
    assert (
        plugin_configs.PluginConfigBrowseResponse.model_validate(browse_body).model_dump(
            exclude_none=True
        )
        == browse_body
    )

    status_body = {
        "state": "running",
        "phase": "download",
        "message": "Downloading plugin",
        "current": 1,
        "total": 2,
        "logs": [{"time": "2026-07-25T10:00:00+00:00", "message": "Started"}],
        "started_at": "2026-07-25T10:00:00+00:00",
        "finished_at": None,
    }
    assert (
        plugin_auto_update.PluginUpdateStatusResponse.model_validate(status_body).model_dump()
        == status_body
    )

    prerequisites = {
        "counterstrikesharp_installed": True,
        "mapchooser_installed": True,
        "maps_file_exists": True,
        "plugin_config_file_exists": True,
        "ready": True,
        "plugin_center_name": map_management.PLUGIN_CENTER_NAME,
        "plugin_center_url": map_management.PLUGIN_CENTER_URL,
        "counterstrikesharp_install_action": "install_counterstrikesharp",
        "maps_path": "/server/maps.txt",
        "plugin_config_path": "/server/config.json",
        "mapchooser_plugin_path": "/server/MapChooser",
    }
    map_body = {
        **prerequisites,
        "content": '"Maplist"\\n{\\n}\\n',
        "revision": "0" * 64,
        "maps": [],
        "config_error": None,
        "message": "maps.txt saved successfully",
    }
    assert map_management.MapsConfigUpdateResponse.model_validate(map_body).model_dump() == map_body
