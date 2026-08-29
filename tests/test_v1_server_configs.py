"""Coverage for the versioned ``/api/v1/server-configs`` import/export contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from modules.models import AuthType, Server
from modules.schemas.servers import (
    ServerConfigExport,
    ServerConfigImportResponse,
    ServerConfigImportResult,
)
from services.server_config_transfer import SECRET_SERVER_FIELDS, server_to_config_entry


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client():
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def _server() -> Server:
    return Server(
        id=41,
        user_id=9,
        name="Production",
        host="192.0.2.10",
        ssh_user="cs2server",
        auth_type=AuthType.PASSWORD,
        ssh_password="ssh-secret",
        sudo_password="sudo-secret",
        server_password="server-secret",
        rcon_password="rcon-secret",
        steam_account_token="STEAMTOKEN",
        discord_webhook_url="https://discord.example/secret",
        status="running",
        api_key="panel-api-key",
    )


def _import_payload(*, include_secrets: bool = False, strategy: str = "skip") -> dict:
    entry = server_to_config_entry(_server(), include_secrets=include_secrets)
    bundle = ServerConfigExport(servers=[entry], include_secrets=include_secrets)
    payload = bundle.model_dump(mode="json")
    payload["conflict_strategy"] = strategy
    return payload


def test_v1_export_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/server-configs")
    assert response.status_code == 401


def test_v1_import_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.post("/api/v1/server-configs", json=_import_payload())
    assert response.status_code == 401


def test_v1_export_redacts_secrets_by_default(monkeypatch):
    client, _user = _client()

    async def fake_resolve(*_args, **_kwargs):
        return [_server()]

    monkeypatch.setattr(
        "api.routes.servers.transfer._resolve_export_servers",
        fake_resolve,
    )
    response = client.get("/api/v1/server-configs")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "upkk-cs2-server-config"
    assert body["version"] == 1
    assert body["include_secrets"] is False
    entry = body["servers"][0]
    assert entry["name"] == "Production"
    assert entry["host"] == "192.0.2.10"
    assert set(entry["redacted_fields"]) == SECRET_SERVER_FIELDS
    for field in SECRET_SERVER_FIELDS:
        assert entry[field] is None
    serialized = str(body)
    assert "ssh-secret" not in serialized
    assert "STEAMTOKEN" not in serialized
    assert "discord.example/secret" not in serialized
    assert "panel-api-key" not in serialized
    assert "id" not in entry
    assert "api_key" not in entry
    assert "status" not in entry


def test_v1_export_includes_secrets_when_requested(monkeypatch):
    client, _user = _client()

    async def fake_resolve(*_args, **_kwargs):
        return [_server()]

    monkeypatch.setattr(
        "api.routes.servers.transfer._resolve_export_servers",
        fake_resolve,
    )
    response = client.get("/api/v1/server-configs?include_secrets=true")
    assert response.status_code == 200
    body = response.json()
    assert body["include_secrets"] is True
    entry = body["servers"][0]
    assert entry["ssh_password"] == "ssh-secret"
    assert entry["rcon_password"] == "rcon-secret"
    assert entry["steam_account_token"] == "STEAMTOKEN"
    assert entry["discord_webhook_url"] == "https://discord.example/secret"
    assert entry["redacted_fields"] == []


def test_v1_export_forwards_selected_server_ids(monkeypatch):
    client, _user = _client()
    captured = {}

    async def fake_resolve(_db, _user, server_ids):
        captured["server_ids"] = server_ids
        return [_server()]

    monkeypatch.setattr(
        "api.routes.servers.transfer._resolve_export_servers",
        fake_resolve,
    )
    response = client.get("/api/v1/server-configs?server_ids=41&server_ids=42")
    assert response.status_code == 200
    assert captured["server_ids"] == [41, 42]
    assert response.json()["servers"][0]["name"] == "Production"


def test_v1_export_empty_list_is_404(monkeypatch):
    client, _user = _client()

    async def fake_resolve(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        "api.routes.servers.transfer._resolve_export_servers",
        fake_resolve,
    )
    response = client.get("/api/v1/server-configs")
    assert response.status_code == 404
    assert response.json()["detail"] == "No servers to export"


def test_v1_export_rejects_empty_id_selection(monkeypatch):
    client, _user = _client()
    response = client.get("/api/v1/server-configs?server_ids=")
    assert response.status_code in {400, 422}


def test_v1_import_returns_summary(monkeypatch):
    client, _user = _client()
    captured = {}

    async def fake_import(request, _db, current_user, _http_request):
        captured["strategy"] = request.conflict_strategy
        captured["count"] = len(request.servers)
        captured["user_id"] = current_user.id
        return ServerConfigImportResponse(
            total=1,
            imported=1,
            updated=0,
            skipped=0,
            failed=0,
            results=[
                ServerConfigImportResult(
                    index=1,
                    name="Production",
                    action="imported",
                    server_id=9,
                )
            ],
        )

    monkeypatch.setattr(
        "api.routes.v1.server_configs.legacy.import_server_configs",
        fake_import,
    )
    response = client.post(
        "/api/v1/server-configs",
        json=_import_payload(strategy="rename"),
    )
    assert response.status_code == 200
    body = response.json()
    assert captured["strategy"] == "rename"
    assert captured["count"] == 1
    assert captured["user_id"] == 1
    assert body["imported"] == 1
    assert body["results"][0]["action"] == "imported"
    assert body["results"][0]["server_id"] == 9


def test_v1_import_rejects_invalid_bundle():
    client, _user = _client()
    response = client.post(
        "/api/v1/server-configs",
        json={"format": "not-a-bundle", "servers": []},
    )
    assert response.status_code == 422
