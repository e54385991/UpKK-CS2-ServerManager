"""Regression coverage for persistent user-requested server stops."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routes import actions
from api.routes.actions import common as action_common
from modules.models import AuthType, Server, ServerStatus
from modules.schemas import ServerAction
from services import ai_tools
from services.redis_manager import redis_manager
from services.scheduled_task_service import scheduled_task_service
from services.server_lifecycle_policy import (
    MANUAL_STOP_BLOCK_REASON,
    apply_user_lifecycle_intent,
    automatic_start_block_reason,
)
from services.server_monitor import ServerMonitor


def server_fixture(**overrides) -> Server:
    values = {
        "id": 91,
        "user_id": 7,
        "name": "Manual stop policy",
        "host": "127.0.0.1",
        "ssh_user": "steam",
        "auth_type": AuthType.PASSWORD,
        "status": ServerStatus.RUNNING,
        "enable_panel_monitoring": True,
        "auto_restart_on_crash": True,
    }
    values.update(overrides)
    return Server(**values)


class FakeSession:
    def __init__(self, server):
        self.server = server
        self.commit_calls = 0
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get(self, model, object_id):
        if model is Server and object_id == self.server.id:
            return self.server
        return None

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, _value):
        return None


class AsyncContextLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class ScheduledLockHandle(AsyncContextLock):
    def __init__(self):
        self.acquired = False

    async def acquire(self):
        self.acquired = True
        return True


def test_user_lifecycle_intent_is_separate_from_observed_status():
    server = SimpleNamespace(status=ServerStatus.RUNNING, manual_stop_requested=False)

    assert apply_user_lifecycle_intent(server, "stop") is True
    assert server.status == ServerStatus.RUNNING
    assert automatic_start_block_reason(server) == MANUAL_STOP_BLOCK_REASON

    assert apply_user_lifecycle_intent(server, "restart") is True
    assert server.manual_stop_requested is False
    assert automatic_start_block_reason(server) is None


@pytest.mark.asyncio
async def test_single_stop_persists_intent_before_ssh_and_keeps_it_on_failure(monkeypatch):
    server = server_fixture()
    db = FakeSession(server)

    class Manager:
        async def stop_server(self, current_server):
            assert current_server.manual_stop_requested is True
            assert db.commit_calls >= 2  # deployment log, then lifecycle intent
            return False, "shutdown timed out"

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr(actions, "SSHManager", Manager)
    monkeypatch.setattr(actions.redis_manager, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(actions.redis_manager, "clear_deployment_progress", no_op)
    monkeypatch.setattr(actions.redis_manager, "set_server_status", no_op)
    monkeypatch.setattr(actions, "send_deployment_update", no_op)
    monkeypatch.setattr(actions, "send_discord_action_notification", no_op)

    response = await actions.server_action(
        server.id,
        ServerAction(action="stop"),
        db,
        SimpleNamespace(id=server.user_id, is_admin=False),
        server,
        MagicMock(),
    )

    assert response.success is False
    assert server.manual_stop_requested is True
    assert server.status == ServerStatus.ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "restart"])
async def test_single_start_and_restart_clear_intent_before_ssh(monkeypatch, action):
    server = server_fixture(
        status=ServerStatus.STOPPED,
        manual_stop_requested=True,
    )
    db = FakeSession(server)

    class Manager:
        async def check_session_manager_available(self, current_server):
            assert current_server.manual_stop_requested is False
            return True, "ready"

        async def stop_server(self, current_server):
            assert current_server.manual_stop_requested is False
            return True, "stopped"

        async def start_server(self, current_server, progress_callback=None):
            assert current_server.manual_stop_requested is False
            assert db.commit_calls >= 2
            return False, "start failed"

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr(actions, "SSHManager", Manager)
    monkeypatch.setattr(actions.redis_manager, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(actions.redis_manager, "clear_deployment_progress", no_op)
    monkeypatch.setattr(actions.redis_manager, "set_server_status", no_op)
    monkeypatch.setattr(actions, "send_deployment_update", no_op)
    monkeypatch.setattr(actions, "send_discord_action_notification", no_op)
    monkeypatch.setattr(actions.asyncio, "sleep", no_op)

    response = await actions.server_action(
        server.id,
        ServerAction(action=action),
        db,
        SimpleNamespace(id=server.user_id, is_admin=False),
        server,
        MagicMock(),
    )

    assert response.success is False
    assert server.manual_stop_requested is False


@pytest.mark.asyncio
async def test_batch_stop_persists_intent_before_ssh_and_keeps_it_on_failure(monkeypatch):
    server = server_fixture()
    db = FakeSession(server)
    enqueue = AsyncMock(return_value={"operation_id": "op-stop", "status": "queued"})
    wait = AsyncMock(return_value={"success": False, "message": "shutdown timed out"})

    monkeypatch.setattr(action_common, "async_session_maker", lambda: db)
    monkeypatch.setattr(
        action_common.Server,
        "get_by_id_and_user",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(
        action_common.redis_manager,
        "set_batch_action_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.operation_runner.enqueue_server_operation",
        enqueue,
    )
    monkeypatch.setattr(
        "services.server_operation_hub.server_operation_hub.wait_until_terminal",
        wait,
    )

    def unexpected_manager(*_args, **_kwargs):
        raise AssertionError("batch stop must enqueue instead of calling SSHManager")

    monkeypatch.setattr(action_common, "SSHManager", unexpected_manager)

    await action_common.execute_single_server_action(
        server.id,
        "stop",
        server.user_id,
        False,
        "batch-id",
    )

    assert server.manual_stop_requested is True
    assert db.commit_calls >= 1
    enqueue.assert_awaited_once()
    wait.assert_awaited_once_with("op-stop")


@pytest.mark.asyncio
async def test_approved_ai_stop_persists_intent_before_ssh(monkeypatch):
    server = server_fixture()
    db = FakeSession(server)

    class Manager:
        async def stop_server(self, current_server):
            assert current_server.manual_stop_requested is True
            assert db.commit_calls >= 1
            return False, "shutdown timed out"

    async def emit(*args, **kwargs):
        return None

    monkeypatch.setattr(ai_tools, "_require_current_server", AsyncMock(return_value=server))
    monkeypatch.setattr(ai_tools, "SSHManager", Manager)
    monkeypatch.setattr(
        ai_tools.maintenance_lock_service,
        "get",
        lambda *args, **kwargs: AsyncContextLock(),
    )
    ctx = ai_tools.ToolContext(
        db=db,
        user=SimpleNamespace(id=server.user_id),
        server=server,
        emit=emit,
    )

    result = await ai_tools.control_server(
        ctx,
        ai_tools.ServerControlInput(action="stop"),
    )

    assert result["success"] is False
    assert server.manual_stop_requested is True


@pytest.mark.asyncio
async def test_scheduled_start_is_skipped_before_ssh(monkeypatch):
    server = server_fixture(manual_stop_requested=True)
    db = FakeSession(server)
    task = SimpleNamespace(id=12, server_id=server.id, action="start")
    lock = ScheduledLockHandle()
    update_status = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "services.scheduled_task_service.async_session_maker",
        lambda: db,
    )
    monkeypatch.setattr(
        "services.scheduled_task_service.maintenance_lock_service.get",
        lambda *args, **kwargs: lock,
    )
    monkeypatch.setattr(scheduled_task_service, "_update_task_status", update_status)
    enqueue = AsyncMock()
    monkeypatch.setattr(
        "services.operation_enqueue.enqueue_server_operation",
        enqueue,
    )

    def unexpected_manager():
        raise AssertionError("SSH manager must not be created for a blocked scheduled start")

    monkeypatch.setattr("services.scheduled_task_service.SSHManager", unexpected_manager)

    await scheduled_task_service._execute_task(task)

    assert lock.acquired is False
    enqueue.assert_not_awaited()
    update_status.assert_awaited_once_with(task.id, "skipped", MANUAL_STOP_BLOCK_REASON)


@pytest.mark.asyncio
async def test_scheduled_stop_does_not_create_manual_intent():
    server = server_fixture(manual_stop_requested=False)

    class Manager:
        async def stop_server(self, _server):
            return True, "stopped"

    success, _ = await scheduled_task_service._execute_action(Manager(), server, "stop")

    assert success is True
    assert server.manual_stop_requested is False


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_a2s_monitoring", [False, True])
async def test_monitor_pauses_queries_while_manual_stop_is_active(
    monkeypatch,
    enable_a2s_monitoring,
):
    server = SimpleNamespace(
        id=91,
        enable_panel_monitoring=True,
        manual_stop_requested=True,
        enable_a2s_monitoring=enable_a2s_monitoring,
        a2s_check_interval_seconds=15,
        monitor_interval_seconds=10,
    )
    db = FakeSession(server)
    monitor = ServerMonitor()
    manager = SimpleNamespace(get_server_status=AsyncMock())
    a2s_check = AsyncMock()

    async def cancel_on_sleep(_delay):
        raise asyncio.CancelledError

    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    monkeypatch.setattr(redis_manager, "append_monitoring_log", AsyncMock(return_value=True))
    monkeypatch.setattr("services.a2s_query.a2s_service.check_server_health", a2s_check)
    monkeypatch.setattr("services.server_monitor.asyncio.sleep", cancel_on_sleep)

    with pytest.raises(asyncio.CancelledError):
        await monitor.monitor_server(server.id, manager)

    manager.get_server_status.assert_not_awaited()
    a2s_check.assert_not_awaited()


@pytest.mark.asyncio
async def test_guard_rechecks_manual_stop_under_operation_lock(monkeypatch):
    fresh_server = server_fixture(manual_stop_requested=True)
    db = FakeSession(fresh_server)
    monitor = ServerMonitor()
    manager = SimpleNamespace(
        check_session_manager_available=AsyncMock(),
        stop_server=AsyncMock(),
        start_server=AsyncMock(),
    )

    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    monkeypatch.setattr(
        "services.server_monitor.maintenance_lock_service.get",
        lambda *args, **kwargs: AsyncContextLock(),
    )
    monkeypatch.setattr(
        "services.plugin_diagnostic_service.has_diagnostic_blocker",
        AsyncMock(return_value=False),
    )

    success, message, returned_server = await monitor._perform_guarded_restart(
        fresh_server.id,
        manager,
    )

    assert success is None
    assert message == MANUAL_STOP_BLOCK_REASON
    assert returned_server is fresh_server
    manager.check_session_manager_available.assert_not_awaited()
    manager.stop_server.assert_not_awaited()
    manager.start_server.assert_not_awaited()
