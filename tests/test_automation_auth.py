"""Scoped personal-key authentication through the real FastAPI dependency graph."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.application import create_app
from api.routes.v1.schemas import ServerDetail
from modules import Server, User, create_access_token, get_db


@pytest.fixture
def automation(monkeypatch):
    user = SimpleNamespace(
        id=7, username="operator", email="operator@example.com", is_admin=False, is_active=True
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user)),
    )
    app = create_app(lifespan=None)

    async def database():
        yield session

    app.dependency_overrides[get_db] = database
    lookup = AsyncMock(side_effect=lambda db, key: user if key == "personal-key" else None)
    monkeypatch.setattr(User, "get_by_api_key", lookup)
    values = dict(
        id=12,
        user_id=7,
        name="alpha",
        host="10.0.0.1",
        game_port=27015,
        ssh_port=22,
        ssh_user="steam",
        game_directory="/srv/cs2",
        game_mode="competitive",
        game_type="0",
        session_manager="tmux",
        status="running",
        default_map="de_dust2",
        max_players=32,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    server = SimpleNamespace(**values)
    monkeypatch.setattr(
        Server,
        "get_by_id_and_user",
        AsyncMock(
            side_effect=lambda db, server_id, user_id: (
                server if server_id == 12 and user_id == 7 else None
            )
        ),
    )
    monkeypatch.setattr(
        Server,
        "get_by_id",
        AsyncMock(side_effect=lambda db, server_id: server if server_id == 12 else None),
    )
    monkeypatch.setattr(Server, "get_all_by_user", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "api.routes.v1.servers._to_detail",
        AsyncMock(
            return_value=ServerDetail(
                **{key: value for key, value in values.items() if key != "user_id"},
                server_name="alpha",
            )
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.server_operation_hub.get_current", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("api.routes.v1.operations.redis_manager.get", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "api.routes.v1.operations.maintenance_lock_service.is_locked", AsyncMock(return_value=False)
    )
    record = dict(
        operation_id=str(uuid4()),
        server_id=12,
        action="restart",
        status="queued",
        actor_user_id=7,
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        success=None,
        message=None,
        server_status=None,
    )
    enqueue = AsyncMock(return_value=record)
    monkeypatch.setattr("api.routes.v1.operations.enqueue_server_operation", enqueue)
    monkeypatch.setattr(
        "api.routes.v1.operations.server_operation_hub.get", AsyncMock(return_value=record)
    )
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            user=user,
            lookup=lookup,
            enqueue=enqueue,
            record=record,
            key={"X-API-Key": "personal-key"},
        )


def test_key_reads_identity_and_servers_without_bearer(automation):
    state = automation
    response = state.client.get("/api/v1/auth/me", headers=state.key)
    assert response.status_code == 200
    assert response.json() == dict(
        id=7, username="operator", email="operator@example.com", is_admin=False, is_active=True
    )
    assert state.client.get("/api/v1/servers", headers=state.key).json() == []
    response = state.client.get("/api/v1/servers/12", headers=state.key)
    assert response.status_code == 200
    assert response.json()["id"] == 12
    assert "api_key" not in response.json()
    assert "ssh_password" not in response.json()


def test_key_uses_existing_queue_and_operation_status(automation):
    state = automation
    response = state.client.post(
        "/api/v1/servers/12/operations", headers=state.key, json={"action": "restart"}
    )
    assert response.status_code == 202
    assert response.json()["operation_id"] == state.record["operation_id"]
    assert response.json()["status"] == "queued"
    assert state.enqueue.await_args.kwargs["actor_user_id"] == 7
    assert state.client.get("/api/v1/servers/12/operations/current", headers=state.key).json() == {
        "operation": None
    }
    response = state.client.get(
        f"/api/v1/servers/12/operations/{state.record['operation_id']}", headers=state.key
    )
    assert response.status_code == 200
    assert response.json()["operation_id"] == state.record["operation_id"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/me",
        "/api/v1/servers",
        "/api/v1/servers/12",
        "/api/v1/servers/12/operations/current",
    ],
)
def test_missing_invalid_revoked_and_inactive_keys_fail(automation, path):
    state = automation
    for headers in ({}, {"X-API-Key": "invalid"}):
        assert state.client.get(path, headers=headers).status_code == 401
    state.lookup.side_effect = None
    state.lookup.return_value = None
    assert state.client.get(path, headers=state.key).status_code == 401
    state.lookup.return_value = state.user
    state.user.is_active = False
    assert state.client.get(path, headers=state.key).status_code == 401


def test_owner_key_cannot_access_other_servers_or_fleet(automation):
    state = automation
    assert state.client.get("/api/v1/servers?scope=all", headers=state.key).status_code == 403
    assert state.client.get("/api/v1/servers/13", headers=state.key).status_code == 404
    assert (
        state.client.get("/api/v1/servers/13/operations/current", headers=state.key).status_code
        == 404
    )
    assert (
        state.client.post(
            "/api/v1/servers/13/operations", headers=state.key, json={"action": "restart"}
        ).status_code
        == 404
    )
    state.enqueue.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/profile", None),
        ("GET", "/api/v1/profile/api-key", None),
        ("POST", "/api/v1/profile/api-key", {}),
        ("DELETE", "/api/v1/profile/api-key", None),
        ("POST", "/api/v1/servers", {}),
        ("PATCH", "/api/v1/servers/12", {}),
        ("DELETE", "/api/v1/servers/12", None),
        ("POST", "/api/v1/servers/12/operations/11111111-2222-4333-8444-555555555555/cancel", {}),
    ],
)
def test_key_does_not_open_unrelated_routes_even_for_admins(automation, method, path, body):
    state = automation
    state.user.is_admin = True
    state.lookup.reset_mock()
    response = state.client.request(method, path, headers=state.key, json=body)
    assert response.status_code == 401
    state.lookup.assert_not_awaited()


@pytest.mark.parametrize("authorization", ["Bearer invalid", "Bearer", "Basic invalid", ""])
def test_invalid_bearer_does_not_fall_back_to_key(automation, authorization):
    state = automation
    response = state.client.get(
        "/api/v1/auth/me", headers={**state.key, "Authorization": authorization}
    )
    assert response.status_code == 401
    state.lookup.assert_not_awaited()


def test_valid_jwt_authentication_remains_supported(automation):
    state = automation
    token = create_access_token({"sub": "7"})
    response = state.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["id"] == 7
    state.lookup.assert_not_awaited()


def test_only_documented_endpoints_opt_in_to_keys(automation):
    opted_in = set()
    for path, methods in automation.app.openapi()["paths"].items():
        for method, operation in methods.items():
            for requirement in operation.get("security", []):
                if "personal_api_key" in requirement.get("OAuth2PasswordBearer", []):
                    opted_in.add((method.upper(), path))
    assert opted_in == {
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/servers"),
        ("GET", "/api/v1/servers/{server_id}"),
        ("POST", "/api/v1/servers/{server_id}/operations"),
        ("GET", "/api/v1/servers/{server_id}/operations/current"),
        ("GET", "/api/v1/servers/{server_id}/operations/{operation_id}"),
    }
