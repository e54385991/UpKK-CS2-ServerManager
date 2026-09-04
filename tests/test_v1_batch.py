"""Coverage for versioned fleet batch actions and owner-only authorization."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.application import create_app
from api.dependencies import get_bearer_or_cookie_user
from api.routes.actions import common as action_common
from api.routes.v1.operation_runner import server as server_runner
from modules import get_current_active_user, get_current_user, get_db
from services.server_operation_hub import server_operation_hub


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client(*, admin: bool = False):
    app = create_app(lifespan=None)
    user = SimpleNamespace(
        id=1,
        username="admin" if admin else "owner",
        is_admin=admin,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_bearer_or_cookie_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def test_v1_batch_actions_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.post(
        "/api/v1/servers/batch-actions",
        json={"server_ids": [1], "action": "restart"},
    )
    assert response.status_code == 401


def test_v1_batch_actions_returns_202_for_owned_servers(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.batch.authorized_server_ids",
        AsyncMock(return_value=[1, 3]),
    )
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.set_batch_action_meta",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.set_batch_action_statuses",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("api.routes.v1.batch._reserve_batch_capacity", AsyncMock())
    monkeypatch.setattr("api.routes.v1.batch._store_task", lambda _task: None)

    def _fake_create_task(coro):
        coro.close()
        return SimpleNamespace(cancel=lambda: None)

    monkeypatch.setattr("api.routes.v1.batch.asyncio.create_task", _fake_create_task)
    monkeypatch.setattr(
        "api.routes.v1.batch.record_audit_event",
        AsyncMock(return_value=None),
    )
    response = client.post(
        "/api/v1/servers/batch-actions",
        json={"server_ids": [1, 2, 3], "action": "restart"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["server_count"] == 2
    assert body["accepted_server_ids"] == [1, 3]
    assert body["action"] == "restart"
    assert body["batch_id"]
    assert body["stream_url"].endswith(f"/batch-actions/{body['batch_id']}/events")


def test_v1_batch_actions_rejects_unowned_servers(monkeypatch):
    client, _user = _client(admin=True)
    monkeypatch.setattr(
        "api.routes.v1.batch.authorized_server_ids",
        AsyncMock(return_value=[]),
    )
    response = client.post(
        "/api/v1/servers/batch-actions",
        json={"server_ids": [2], "action": "stop"},
    )
    assert response.status_code == 400


def test_v1_batch_journal_is_actor_only(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.get_batch_action_meta",
        AsyncMock(return_value={"actor_user_id": 9, "action": "restart"}),
    )
    response = client.get("/api/v1/servers/batch-actions/deadbeef")
    assert response.status_code == 404


def test_v1_batch_journal_returns_summary(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.get_batch_action_meta",
        AsyncMock(return_value={"actor_user_id": 1, "action": "restart"}),
    )
    monkeypatch.setattr(
        "api.routes.v1.batch.redis_manager.get_batch_action_status",
        AsyncMock(
            return_value={
                "1": {"status": "success", "message": "ok"},
                "3": {"status": "in_progress", "message": "working"},
            }
        ),
    )
    response = client.get("/api/v1/servers/batch-actions/abc123")
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "restart"
    assert body["summary"]["total"] == 2
    assert body["summary"]["succeeded"] == 1
    assert body["summary"]["is_complete"] is False


@pytest.mark.asyncio
async def test_batch_send_command_delegates_execution_to_server_queue(monkeypatch):
    server = SimpleNamespace(id=1, user_id=1)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

        async def get(self, _model, server_id):
            assert server_id == server.id
            return server

    status_updates = AsyncMock()
    enqueue = AsyncMock(
        return_value={"operation_id": "queued-command-1"},
    )
    wait_until_terminal = AsyncMock(
        return_value={"success": True, "message": "Executed successfully"},
    )
    monkeypatch.setattr(action_common, "async_session_maker", lambda: Session())
    monkeypatch.setattr(action_common.redis_manager, "set_batch_action_status", status_updates)
    monkeypatch.setattr(
        "api.routes.v1.operation_runner.enqueue_game_console_command",
        enqueue,
    )
    monkeypatch.setattr(
        server_operation_hub,
        "wait_until_terminal",
        wait_until_terminal,
    )

    await action_common.execute_single_server_command(
        server.id,
        "status",
        user_id=server.user_id,
        is_admin=False,
        batch_id="batch-1",
    )

    enqueue.assert_awaited_once_with(
        server_id=server.id,
        command="status",
        actor_user_id=server.user_id,
    )
    wait_until_terminal.assert_awaited_once_with("queued-command-1")
    assert status_updates.await_count == 3
    assert status_updates.await_args_list[-1].args == (
        "batch-1",
        server.id,
        "success",
        "Executed successfully",
    )


@pytest.mark.asyncio
async def test_queued_game_console_command_runs_only_after_hub_dispatch(monkeypatch):
    record = {
        "operation_id": "queued-command-2",
        "server_id": 1,
        "actor_user_id": 1,
    }
    user = SimpleNamespace(id=1, is_active=True)
    server = SimpleNamespace(id=1)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

        async def get(self, _model, user_id):
            assert user_id == user.id
            return user

    class Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

    get_record = AsyncMock(return_value=record)
    mark_running = AsyncMock()
    emit = AsyncMock()
    finish = AsyncMock()
    execute = AsyncMock(return_value={"success": True, "message": "Command sent"})
    monkeypatch.setattr(server_runner.server_operation_hub, "get", get_record)
    monkeypatch.setattr(server_runner.server_operation_hub, "mark_running", mark_running)
    monkeypatch.setattr(server_runner.server_operation_hub, "emit", emit)
    monkeypatch.setattr(server_runner.server_operation_hub, "finish", finish)
    monkeypatch.setattr(server_runner, "async_session_maker", lambda: Session())
    monkeypatch.setattr(
        server_runner,
        "require_server_access",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(
        server_runner.maintenance_lock_service,
        "get",
        lambda *_args, **_kwargs: Lock(),
    )
    monkeypatch.setattr(server_runner, "execute_custom_commands", execute)

    await server_runner.run_game_console_command(
        operation_id=record["operation_id"],
        command="status",
    )

    mark_running.assert_awaited_once_with(record["operation_id"])
    execute.assert_awaited_once_with(server, "game_process", "status")
    finish.assert_awaited_once_with(
        record["operation_id"],
        success=True,
        message="Command sent",
    )
