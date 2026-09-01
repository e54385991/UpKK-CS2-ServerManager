"""Coverage for the versioned ``/api/v1/servers/{id}/cleanup`` contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from api.dependencies import get_bearer_or_cookie_user
from modules import get_current_active_user, get_current_user, get_db


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client():
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_bearer_or_cookie_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


def test_v1_cleanup_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    assert client.get("/api/v1/servers/7/cleanup/scan").status_code == 401
    assert (
        client.post(
            "/api/v1/servers/7/cleanup/delete",
            json={"mode": "safe"},
        ).status_code
        == 401
    )


def test_v1_cleanup_scan_projects_candidates(monkeypatch):
    client = _client()

    async def fake_scan(*_args, **_kwargs):
        return {
            "safe_items": [
                {
                    "path": "cs2/game/csgo/logs/error.log",
                    "name": "error.log",
                    "type": "file",
                    "size": 128,
                    "category": "logs",
                    "reason": "log file",
                    "danger_level": "safe",
                }
            ],
            "archive_items": [],
            "workshop_summary": {
                "path": "cs2/steamapps/workshop",
                "item_count": 2,
                "size": 4096,
            },
            "total_size": 4224,
        }

    monkeypatch.setattr(
        "api.routes.v1.cleanup.legacy.scan_server_cleanup",
        fake_scan,
    )
    response = client.get("/api/v1/servers/7/cleanup/scan")
    assert response.status_code == 200
    body = response.json()
    assert body["total_size"] == 4224
    assert body["safe_items"][0]["path"] == "cs2/game/csgo/logs/error.log"
    assert body["workshop_summary"]["item_count"] == 2
    assert body["safe_item_count"] == 1
    assert body["truncated"] is False
    assert "ssh_password" not in str(body)


def test_v1_cleanup_delete_enqueues_hub(monkeypatch):
    client = _client()
    record = {
        "operation_id": "op-clean-1",
        "server_id": 7,
        "action": "cleanup_delete",
        "status": "queued",
        "success": None,
        "message": None,
        "server_status": None,
        "actor_user_id": 1,
        "started_at": "2026-09-01T00:00:00+00:00",
        "completed_at": None,
        "command": "cleanup delete safe",
    }
    monkeypatch.setattr(
        "api.routes.v1.cleanup.get_server_with_permission",
        AsyncMock(return_value=SimpleNamespace(id=7)),
    )
    monkeypatch.setattr("api.routes.v1.cleanup.reject_stuck_lock_unless_active", AsyncMock())
    enqueue = AsyncMock(return_value=record)
    monkeypatch.setattr("api.routes.v1.cleanup.enqueue_cleanup_delete", enqueue)
    monkeypatch.setattr("api.routes.v1.cleanup.record_audit_event", AsyncMock())
    response = client.post(
        "/api/v1/servers/7/cleanup/delete",
        json={"mode": "safe", "paths": []},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == "op-clean-1"
    assert body["action"] == "cleanup_delete"
    assert enqueue.await_args.kwargs["mode"] == "safe"


def test_v1_cleanup_system_apply_enqueues_hub(monkeypatch):
    client = _client()
    record = {
        "operation_id": "op-sys-1",
        "server_id": 7,
        "action": "cleanup_system",
        "status": "queued",
        "success": None,
        "message": None,
        "server_status": None,
        "actor_user_id": 1,
        "started_at": "2026-09-01T00:00:00+00:00",
        "completed_at": None,
        "command": "cleanup system journal",
    }
    monkeypatch.setattr(
        "api.routes.v1.cleanup.get_server_with_permission",
        AsyncMock(return_value=SimpleNamespace(id=7)),
    )
    monkeypatch.setattr("api.routes.v1.cleanup.reject_stuck_lock_unless_active", AsyncMock())
    enqueue = AsyncMock(return_value=record)
    monkeypatch.setattr("api.routes.v1.cleanup.enqueue_cleanup_system", enqueue)
    monkeypatch.setattr("api.routes.v1.cleanup.record_audit_event", AsyncMock())
    response = client.post(
        "/api/v1/servers/7/cleanup/system",
        json={"targets": ["journal"]},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == "op-sys-1"
    assert body["action"] == "cleanup_system"
    assert enqueue.await_args.kwargs["targets"] == ["journal"]


def test_v1_cleanup_policy_and_system_require_authentication():
    client = TestClient(create_app(lifespan=None))
    assert client.get("/api/v1/servers/7/cleanup/policy").status_code == 401
    assert client.get("/api/v1/servers/7/cleanup/system").status_code == 401
    assert client.get("/api/v1/servers/7/cleanup/scan/events").status_code == 401
    assert client.get("/api/v1/servers/7/cleanup/system/events").status_code == 401
    assert (
        client.post(
            "/api/v1/servers/7/cleanup/system",
            json={"targets": ["journal"]},
        ).status_code
        == 401
    )


def test_v1_cleanup_scan_events_stream_phases(monkeypatch):
    client = _client()

    async def fake_load(*_args, **_kwargs):
        return SimpleNamespace(id=7, game_directory="/home/cs2server/cs2")

    async def fake_iter(*_args, **_kwargs):
        yield {"type": "phase", "phase": "logs", "message": "Scanning leftover log files"}
        yield {"type": "heartbeat"}
        yield {"type": "batch", "category": "safe", "phase": "logs", "found": 2, "size": 10}
        yield {
            "type": "done",
            "data": {
                "safe_items": [],
                "archive_items": [],
                "workshop_summary": {"path": "/ws", "item_count": 0, "size": 0},
                "total_size": 10,
                "safe_item_count": 2,
                "archive_item_count": 0,
                "truncated": True,
            },
        }

    monkeypatch.setattr("api.routes.v1.cleanup._load_stream_server", fake_load)
    monkeypatch.setattr("api.routes.v1.cleanup.game_cleanup_service.iter_scan", fake_iter)
    response = client.get("/api/v1/servers/7/cleanup/scan/events")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: phase" in response.text
    assert "event: batch" in response.text
    assert "event: done" in response.text
    assert "keep-alive" in response.text
    assert '"truncated": true' in response.text
    assert "ssh_password" not in response.text


def test_v1_cleanup_system_events_stream_targets(monkeypatch):
    client = _client()

    async def fake_load(*_args, **_kwargs):
        return SimpleNamespace(id=7, game_directory="/home/cs2server/cs2")

    async def fake_iter(*_args, **_kwargs):
        yield {"type": "phase", "phase": "privilege", "message": "SSH privilege: none"}
        yield {
            "type": "done",
            "data": {
                "privilege": "none",
                "retain_days": 7,
                "has_sudo_password": False,
                "targets": [],
                "total_size": 0,
                "can_apply_privileged": False,
                "manual_execute": [],
                "manual_setup": [],
            },
        }

    monkeypatch.setattr("api.routes.v1.cleanup._load_stream_server", fake_load)
    monkeypatch.setattr("api.routes.v1.cleanup.system_cleanup_service.iter_scan", fake_iter)
    response = client.get("/api/v1/servers/7/cleanup/system/events")
    assert response.status_code == 200
    assert "event: phase" in response.text
    assert "event: done" in response.text
    assert '"privilege": "none"' in response.text


def test_v1_cleanup_policy_save_returns_manual_commands_without_sudo(monkeypatch):
    client = _client()
    server = SimpleNamespace(
        id=7,
        cleanup_auto_enabled=False,
        cleanup_retain_days=7,
        cleanup_targets=["game_logs"],
        sudo_password=None,
    )

    async def fake_get_server(*_args, **_kwargs):
        return server

    monkeypatch.setattr("api.routes.v1.cleanup.get_server_with_permission", fake_get_server)
    monkeypatch.setattr("api.routes.v1.cleanup._cleanup_task", AsyncMock(return_value=None))

    response = client.put(
        "/api/v1/servers/7/cleanup/policy",
        json={
            "enabled": True,
            "retain_days": 7,
            "schedule_value": "03:30",
            "targets": ["journal", "game_logs"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True
    assert "journalctl --vacuum-time=7d" in "\n".join(body["manual_execute"])
    assert "sudo crontab -e" in "\n".join(body["manual_setup"])
    assert "Host config" in body["message"]
