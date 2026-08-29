"""Coverage for the versioned ``/api/v1/servers/{id}/operations`` contract."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.application import create_app
from api.dependencies import get_bearer_or_cookie_user
from api.routes.v1.schemas import ServerLifecycleAction, ServerOperationAction
from modules import get_current_active_user, get_current_user, get_db
from modules.models.servers import ServerStatus
from modules.schemas.common import ALLOWED_SERVER_ACTIONS
from services.redis_manager import redis_manager
from services.server_operation_hub import server_operation_hub


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _sample_server(**overrides):
    values = {
        "id": 1,
        "name": "alpha",
        "host": "10.0.0.1",
        "game_port": 27015,
        "status": ServerStatus.STOPPED,
        "user_id": 1,
        "ssh_port": 22,
        "ssh_user": "cs2",
        "game_directory": "/home/cs2",
        "game_mode": "competitive",
        "game_type": "0",
        "description": None,
        "default_map": "de_dust2",
        "max_players": 10,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "last_deployed": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _queued_record(*, server_id: int = 1, action: str = "status", actor_user_id: int = 1) -> dict:
    return {
        "operation_id": str(uuid4()),
        "server_id": server_id,
        "action": action,
        "status": "queued",
        "success": None,
        "message": None,
        "server_status": None,
        "actor_user_id": actor_user_id,
        "started_at": "2026-08-29T00:00:00+00:00",
        "completed_at": None,
    }


def _client(*, monkeypatch, admin: bool = False):
    app = create_app(lifespan=None)
    user = SimpleNamespace(
        id=1,
        username="admin" if admin else "owner",
        is_admin=admin,
        is_active=True,
        email="owner@example.com",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_bearer_or_cookie_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    server = _sample_server()
    monkeypatch.setattr(
        "api.routes.v1.operations.require_server_access",
        AsyncMock(return_value=server),
    )
    return TestClient(app), server, user


def setup_function() -> None:
    server_operation_hub._records.clear()
    server_operation_hub._current.clear()
    server_operation_hub._pending.clear()
    server_operation_hub._runners.clear()
    server_operation_hub._events.clear()
    server_operation_hub._queues.clear()
    server_operation_hub._tasks.clear()

    async def clear_redis_current() -> None:
        await redis_manager.delete("server_op_current:1")

    asyncio.run(clear_redis_current())


def test_v1_operation_action_literals_match_legacy_allow_list():
    assert set(ServerLifecycleAction.__args__) == set(ALLOWED_SERVER_ACTIONS)
    assert set(ServerOperationAction.__args__) == set(ALLOWED_SERVER_ACTIONS) | {
        "install_plugin",
        "install_github_plugin",
        "uninstall_github_plugin",
        "apply_apt_mirror",
        "s3_restore",
    }


def test_v1_operations_require_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.post("/api/v1/servers/1/operations", json={"action": "status"})
    assert response.status_code == 401


def test_v1_start_operation_returns_202(monkeypatch):
    client, _server, user = _client(monkeypatch=monkeypatch)
    record = _queued_record(action="start", actor_user_id=user.id)
    monkeypatch.setattr(
        "api.routes.v1.operations.enqueue_server_operation",
        AsyncMock(return_value=record),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.redis_manager.get",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.maintenance_lock_service.is_locked",
        AsyncMock(return_value=False),
    )

    response = client.post("/api/v1/servers/1/operations", json={"action": "start"})
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == record["operation_id"]
    assert body["action"] == "start"
    assert body["status"] == "queued"
    assert body["stream_url"] == (f"/api/v1/servers/1/operations/{record['operation_id']}/events")
    assert "password" not in body
    assert "token" not in body


def test_v1_start_operation_rejects_unknown_action(monkeypatch):
    client, _server, _user = _client(monkeypatch=monkeypatch)
    response = client.post("/api/v1/servers/1/operations", json={"action": "explode"})
    assert response.status_code == 422


def test_v1_start_operation_queues_when_another_job_is_active(monkeypatch):
    client, _server, user = _client(monkeypatch=monkeypatch)
    record = _queued_record(action="start", actor_user_id=user.id)
    enqueue = AsyncMock(return_value=record)
    monkeypatch.setattr(
        "api.routes.v1.operations.redis_manager.get",
        AsyncMock(return_value="1"),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.server_operation_hub.get_current",
        AsyncMock(return_value={"status": "running", "operation_id": "live"}),
    )
    monkeypatch.setattr("api.routes.v1.operations.enqueue_server_operation", enqueue)
    response = client.post("/api/v1/servers/1/operations", json={"action": "start"})
    assert response.status_code == 202
    enqueue.assert_awaited_once()


def test_v1_start_operation_releases_stale_deployment_lock(monkeypatch):
    client, _server, user = _client(monkeypatch=monkeypatch)
    record = _queued_record(action="status", actor_user_id=user.id)
    keys = {"deployment_lock:1": "1"}

    async def fake_get(key):
        return keys.get(key)

    async def fake_delete(key):
        keys.pop(key, None)
        return True

    monkeypatch.setattr("api.routes.v1.operations.redis_manager.get", fake_get)
    monkeypatch.setattr("api.routes.v1.operations.redis_manager.delete", fake_delete)
    monkeypatch.setattr(
        "api.routes.v1.operations.enqueue_server_operation",
        AsyncMock(return_value=record),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.maintenance_lock_service.is_locked",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.server_operation_hub.get_current",
        AsyncMock(return_value={"status": "failed", "operation_id": "old"}),
    )

    response = client.post("/api/v1/servers/1/operations", json={"action": "status"})
    assert response.status_code == 202
    assert "deployment_lock:1" not in keys


def test_v1_get_operation_404_for_unknown_id(monkeypatch):
    client, _server, _user = _client(monkeypatch=monkeypatch)
    response = client.get(f"/api/v1/servers/1/operations/{uuid4()}")
    assert response.status_code == 404


def test_v1_get_current_operation_empty(monkeypatch):
    client, _server, _user = _client(monkeypatch=monkeypatch)
    response = client.get("/api/v1/servers/1/operations/current")
    assert response.status_code == 200
    assert response.json() == {"operation": None}


def test_v1_get_current_and_get_by_id(monkeypatch):
    client, _server, user = _client(monkeypatch=monkeypatch)

    async def seed():
        return await server_operation_hub.create(
            server_id=1,
            action="restart",
            actor_user_id=user.id,
        )

    record = asyncio.run(seed())
    current = client.get("/api/v1/servers/1/operations/current")
    assert current.status_code == 200
    assert current.json()["operation"]["operation_id"] == record["operation_id"]

    detail = client.get(f"/api/v1/servers/1/operations/{record['operation_id']}")
    assert detail.status_code == 200
    assert detail.json()["action"] == "restart"
    assert detail.json()["status"] == "queued"


def test_v1_sse_replays_history_and_closes_on_terminal(monkeypatch):
    client, _server, user = _client(monkeypatch=monkeypatch)

    async def seed():
        record = await server_operation_hub.create(
            server_id=1,
            action="status",
            actor_user_id=user.id,
        )
        await server_operation_hub.emit(
            record["operation_id"],
            "progress",
            kind="status",
            message="Checking server status...",
        )
        await server_operation_hub.finish(
            record["operation_id"],
            success=True,
            message="Server is stopped",
            server_status="stopped",
        )
        return record["operation_id"]

    operation_id = asyncio.run(seed())
    response = client.get(f"/api/v1/servers/1/operations/{operation_id}/events")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "Checking server status..." in response.text
    assert "event: operation_completed" in response.text
    assert "Server is stopped" in response.text


def test_v1_create_emits_accepted_progress():
    async def seed():
        record = await server_operation_hub.create(
            server_id=1,
            action="deploy",
            actor_user_id=1,
        )
        events = await server_operation_hub.replay(record["operation_id"], 0)
        return record, events

    record, events = asyncio.run(seed())
    assert record["status"] == "queued"
    assert events
    assert any("accepted" in str(event.get("message") or "").lower() for event in events)


def test_v1_journal_replays_history(monkeypatch):
    client, _server, user = _client(monkeypatch=monkeypatch)

    async def seed():
        record = await server_operation_hub.create(
            server_id=1,
            action="status",
            actor_user_id=user.id,
        )
        await server_operation_hub.emit(
            record["operation_id"],
            "progress",
            kind="status",
            message="Checking server status...",
        )
        await server_operation_hub.finish(
            record["operation_id"],
            success=True,
            message="Server is stopped",
            server_status="stopped",
        )
        return record["operation_id"]

    operation_id = asyncio.run(seed())
    response = client.get(f"/api/v1/servers/1/operations/{operation_id}/journal")
    assert response.status_code == 200
    body = response.json()
    assert body["operation"]["operation_id"] == operation_id
    messages = [event["message"] for event in body["events"]]
    assert any("accepted" in message.lower() for message in messages)
    assert "Checking server status..." in messages
    assert "Server is stopped" in messages


def test_v1_start_operation_releases_stale_maintenance_lock(monkeypatch):
    client, _server, user = _client(monkeypatch=monkeypatch)
    record = _queued_record(action="status", actor_user_id=user.id)
    locked = {"value": True}
    released = {"n": 0}

    async def is_locked(_server_id):
        return locked["value"]

    async def force_release(_server_id, **_kwargs):
        released["n"] += 1
        locked["value"] = False
        return True

    monkeypatch.setattr(
        "api.routes.v1.operations.enqueue_server_operation",
        AsyncMock(return_value=record),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.redis_manager.get",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.maintenance_lock_service.is_locked",
        is_locked,
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.maintenance_lock_service.force_release_server_lock",
        force_release,
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.server_operation_hub.get_current",
        AsyncMock(return_value={"status": "failed", "operation_id": "old"}),
    )

    response = client.post("/api/v1/servers/1/operations", json={"action": "status"})
    assert response.status_code == 202
    assert released["n"] == 1


def test_v1_sse_unknown_operation_404(monkeypatch):
    client, _server, _user = _client(monkeypatch=monkeypatch)
    response = client.get(f"/api/v1/servers/1/operations/{uuid4()}/events")
    assert response.status_code == 404


def test_v1_logs_are_redacted(monkeypatch):
    client, _server, _user = _client(monkeypatch=monkeypatch)
    log = SimpleNamespace(
        id=9,
        action="start",
        status="failed",
        output='sv_password "super-secret"',
        error_message="rcon_password leaked",
        created_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        "api.routes.v1.operations.DeploymentLog.get_logs_by_server",
        AsyncMock(return_value=[log]),
    )
    response = client.get("/api/v1/servers/1/operations/logs")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["id"] == 9
    assert "super-secret" not in (body[0]["output"] or "")
    assert "leaked" in (body[0]["error_message"] or "") or "[REDACTED]" in str(body[0])


def test_v1_lock_get_and_clear(monkeypatch):
    client, _server, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.operations.redis_manager.get",
        AsyncMock(return_value="1"),
    )
    locked = client.get("/api/v1/servers/1/operations/lock")
    assert locked.status_code == 200
    assert locked.json() == {"lock_active": True, "server_status": "stopped"}

    monkeypatch.setattr(
        "api.routes.v1.operations.cancel_deployment",
        AsyncMock(
            return_value=JSONResponse(
                content={"success": True, "message": "Deployment lock cleared successfully"}
            )
        ),
    )
    cleared = client.delete("/api/v1/servers/1/operations/lock")
    assert cleared.status_code == 200
    assert cleared.json()["success"] is True
