"""Coverage for the versioned ``/api/v1/servers/{id}/maps`` workspace."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.application import create_app
from api.routes.v1.schemas import MapsWorkspaceView, MapSyncView
from modules import get_current_active_user, get_current_user, get_db
from services.map_management_service import (
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    content_revision,
    render_official_maps_config,
)


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _sample_server(**overrides):
    values = {
        "id": 1,
        "name": "ops-verify",
        "game_directory": "/tmp/cs2-ops-verify",
        "map_pool_sync_url": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Unlocked:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _client(monkeypatch, *, server=None):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    row = server or _sample_server()

    async def fake_access(*_args, **_kwargs):
        return row

    monkeypatch.setattr("api.routes.v1.maps.require_server_access", fake_access)
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._get_map_sync_tasks",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "api.routes.v1.maps.maintenance_lock_service.get",
        lambda *_args, **_kwargs: _Unlocked(),
    )
    return TestClient(app), row, user


def _ready_prereqs(**overrides) -> dict:
    payload = {
        "counterstrikesharp_installed": True,
        "mapchooser_installed": True,
        "maps_file_exists": True,
        "plugin_config_file_exists": True,
        "ready": True,
        "plugin_center_name": "CS2-Upkk-PanelPLG-Mapchooser",
        "maps_path": "/tmp/maps.txt",
        "plugin_config_path": "/tmp/config.json",
    }
    payload.update(overrides)
    return payload


def test_v1_maps_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/servers/1/maps")
    assert response.status_code == 401


def test_v1_maps_get_degrades_when_ssh_is_down(monkeypatch):
    client, _server, _user = _client(monkeypatch)

    async def fail_connect(_server):
        raise HTTPException(status_code=502, detail="SSH connection failed: Connection refused")

    monkeypatch.setattr("api.routes.v1.maps.legacy._connect", fail_connect)

    response = client.get("/api/v1/servers/1/maps")
    assert response.status_code == 200
    body = response.json()
    assert body["server_id"] == 1
    assert body["ssh_ok"] is False
    assert body["ready"] is False
    assert body["maps"] == []
    assert "Connection refused" in body["ssh_error"]
    assert body["custom_sync"]["enabled"] is False


def test_v1_maps_get_reports_missing_prerequisites(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr("api.routes.v1.maps.legacy._connect", AsyncMock(return_value=ssh))
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._inspect_prerequisites",
        AsyncMock(
            return_value=_ready_prereqs(
                counterstrikesharp_installed=False,
                mapchooser_installed=False,
                ready=False,
                maps_file_exists=False,
            )
        ),
    )

    response = client.get("/api/v1/servers/1/maps")
    assert response.status_code == 200
    body = response.json()
    assert body["ssh_ok"] is True
    assert body["ready"] is False
    assert body["counterstrikesharp_installed"] is False
    assert body["mapchooser_installed"] is False
    assert body["maps"] == []
    assert body["revision"] is None
    ssh.disconnect.assert_awaited()


def test_v1_maps_get_includes_official_empty_workshop_id(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(disconnect=AsyncMock())
    official = render_official_maps_config(["de_dust2", "cs_office"])
    monkeypatch.setattr("api.routes.v1.maps.legacy._connect", AsyncMock(return_value=ssh))
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._inspect_prerequisites",
        AsyncMock(return_value=_ready_prereqs()),
    )
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._read_maps_config",
        AsyncMock(return_value=(official, True)),
    )
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._read_plugin_config",
        AsyncMock(return_value=(DEFAULT_PLUGIN_CONFIG_CONTENT, True)),
    )

    response = client.get("/api/v1/servers/1/maps")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["revision"] == content_revision(official)
    names = [item["name"] for item in body["maps"]]
    assert names == ["cs_office", "de_dust2"]
    assert all(item["workshop_id"] == "" for item in body["maps"])
    assert body["plugin_config"] is not None
    assert body["plugin_config"]["fields"]


def test_v1_maps_patch_toggles_official_map(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(disconnect=AsyncMock())
    official = render_official_maps_config(["de_dust2"])
    replaced: list[str] = []

    async def fake_replace(_ssh, _server, content):
        replaced.append(content)

    monkeypatch.setattr("api.routes.v1.maps.legacy._connect", AsyncMock(return_value=ssh))
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._inspect_prerequisites",
        AsyncMock(return_value=_ready_prereqs()),
    )
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._read_maps_config",
        AsyncMock(return_value=(official, True)),
    )
    monkeypatch.setattr("api.routes.v1.maps.legacy._replace_maps_config", fake_replace)

    response = client.patch(
        "/api/v1/servers/1/maps",
        json={
            "name": "de_dust2",
            "workshop_id": "",
            "expected_revision": content_revision(official),
            "enabled": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["maps"][0]["name"] == "de_dust2"
    assert body["maps"][0]["workshop_id"] == ""
    assert body["maps"][0]["enabled"] is False
    assert replaced
    assert '"enabled"\t"0"' in replaced[0]


def test_v1_maps_patch_rejects_missing_revision(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    response = client.patch(
        "/api/v1/servers/1/maps",
        json={"name": "de_dust2", "workshop_id": "", "enabled": False},
    )
    assert response.status_code == 422


def test_v1_maps_patch_rejects_stale_revision(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(disconnect=AsyncMock())
    official = render_official_maps_config(["de_dust2"])
    monkeypatch.setattr("api.routes.v1.maps.legacy._connect", AsyncMock(return_value=ssh))
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._inspect_prerequisites",
        AsyncMock(return_value=_ready_prereqs()),
    )
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._read_maps_config",
        AsyncMock(return_value=(official, True)),
    )
    monkeypatch.setattr("api.routes.v1.maps.legacy._replace_maps_config", AsyncMock())

    response = client.patch(
        "/api/v1/servers/1/maps",
        json={
            "name": "de_dust2",
            "workshop_id": "",
            "expected_revision": "a" * 64,
            "enabled": False,
        },
    )
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


def test_v1_maps_delete_official_map(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    ssh = SimpleNamespace(disconnect=AsyncMock())
    official = render_official_maps_config(["de_dust2", "cs_office"])
    replaced: list[str] = []

    async def fake_replace(_ssh, _server, content):
        replaced.append(content)

    monkeypatch.setattr("api.routes.v1.maps.legacy._connect", AsyncMock(return_value=ssh))
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._inspect_prerequisites",
        AsyncMock(return_value=_ready_prereqs()),
    )
    monkeypatch.setattr(
        "api.routes.v1.maps.legacy._read_maps_config",
        AsyncMock(return_value=(official, True)),
    )
    monkeypatch.setattr("api.routes.v1.maps.legacy._replace_maps_config", fake_replace)

    response = client.request(
        "DELETE",
        "/api/v1/servers/1/maps",
        json={
            "name": "de_dust2",
            "workshop_id": "",
            "expected_revision": content_revision(official),
        },
    )
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["maps"]]
    assert names == ["cs_office"]
    assert replaced


def test_v1_maps_add_wraps_legacy_payload(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    official = render_official_maps_config(["de_dust2"])

    async def fake_add(*_args, **_kwargs):
        return {
            **_ready_prereqs(),
            "maps": [
                {
                    "name": "Dust Workshop",
                    "workshop_id": "3070591565",
                    "enabled": True,
                    "filename": "de_dust2_classic",
                    "min_players": "0",
                    "only_nominate": False,
                    "restricted_times": "",
                }
            ],
            "revision": content_revision(official),
            "message": "Added Dust Workshop to maps.txt",
        }

    monkeypatch.setattr("api.routes.v1.maps.legacy.add_map", fake_add)

    response = client.post(
        "/api/v1/servers/1/maps",
        json={"workshop_id": "3070591565", "name": "Dust Workshop"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["maps"][0]["workshop_id"] == "3070591565"
    assert "Added Dust Workshop" in body["message"]


def test_v1_maps_uninstall_mapchooser_wraps_legacy(monkeypatch):
    client, _server, _user = _client(monkeypatch)
    called: list[str] = []

    async def fake_uninstall(_server_id, request, _db, _user):
        called.append(request.confirmation)
        return {"success": True}

    async def fake_workspace(_server, _db):
        return MapsWorkspaceView(
            server_id=1,
            ssh_ok=True,
            ready=False,
            mapchooser_installed=False,
            custom_sync=MapSyncView(
                url="",
                enabled=False,
                interval_seconds=300,
                run_count=0,
            ),
        )

    monkeypatch.setattr(
        "api.routes.v1.maps.legacy.uninstall_mapchooser_plugin",
        fake_uninstall,
    )
    monkeypatch.setattr("api.routes.v1.maps._load_workspace", fake_workspace)

    response = client.request(
        "DELETE",
        "/api/v1/servers/1/maps/plugin",
        json={"confirmation": "UNINSTALL MAPCHOOSER"},
    )
    assert response.status_code == 200
    assert called == ["UNINSTALL MAPCHOOSER"]
    assert response.json()["mapchooser_installed"] is False
    assert "removed" in response.json()["message"]
