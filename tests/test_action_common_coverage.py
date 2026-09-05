"""Coverage for isolated batch-action helpers and worker failure paths."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from services.maintenance_lock import OperationBusyError

common = importlib.import_module("api.routes.actions.common")


class _Session:
    def __init__(self, *, server=None, owner=None):
        self.server = server
        self.owner = owner
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _model, _item_id):
        return self.owner or self.server


class _Lock:
    def __init__(self, error=None):
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *_args):
        return False


def _server(**overrides):
    values = {"id": 4, "user_id": 2, "last_plugin_backup": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def _redis(monkeypatch):
    redis = SimpleNamespace(
        set_batch_action_status=AsyncMock(),
        set_batch_action_statuses=AsyncMock(),
        clear_deployment_progress=AsyncMock(),
    )
    monkeypatch.setattr(common, "redis_manager", redis)
    return redis


@pytest.mark.asyncio
async def test_batch_capacity_bounded_operation_and_notifications(monkeypatch):
    common._pending_batch_counts.clear()
    redis = _redis(monkeypatch)
    called = []
    await common._reserve_batch_capacity(2, 3)
    assert common._pending_batch_counts[2] == 3

    async def callback():
        called.append(True)

    await common._run_bounded_batch_operation(4, 2, "batch", "test", callback)
    assert called == [True]
    assert common._pending_batch_counts[2] == 2
    common._pending_batch_counts.clear()

    common._pending_batch_counts[2] = common.MAX_PENDING_BATCH_OPERATIONS_PER_USER
    with pytest.raises(HTTPException) as caught:
        await common._reserve_batch_capacity(2, 1)
    assert caught.value.status_code == 429
    common._pending_batch_counts.clear()

    lock_factory = SimpleNamespace(get=lambda *_args, **_kwargs: _Lock(OperationBusyError("busy")))
    monkeypatch.setattr(common, "maintenance_lock_service", lock_factory)
    await common._run_bounded_batch_operation(4, 2, "batch", "test", AsyncMock(), acquire_lock=True)
    redis.set_batch_action_status.assert_awaited_once_with("batch", 4, "failed", "busy")

    notifier = SimpleNamespace(queue_notify=Mock())
    monkeypatch.setattr(common, "discord_notification_service", notifier)
    await common.send_discord_action_notification(None, "update", True, "done")
    await common.send_discord_action_notification(_server(), "unknown", True, "done")
    await common.send_discord_action_notification(
        _server(), "update", False, "failed", details={"x": 1}
    )
    notifier.queue_notify.assert_called_once()
    assert notifier.queue_notify.call_args.args[1] == common.EVENT_MANUAL_UPDATE

    monkeypatch.setattr(common.asyncio, "sleep", AsyncMock())
    await common.clear_deployment_progress_after_delay(4, delay=0)
    redis.clear_deployment_progress.assert_awaited_once_with(4)


@pytest.mark.asyncio
async def test_upload_latest_plugin_backup_to_s3_all_paths(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=2)
    s3 = SimpleNamespace(
        is_configured=lambda _owner: False,
        upload_remote_backup=AsyncMock(),
    )
    notify = SimpleNamespace(queue_notify=Mock())
    monkeypatch.setattr(common, "s3_backup_service", s3)
    monkeypatch.setattr(common, "discord_notification_service", notify)
    db = _Session(server=server)
    ssh = SimpleNamespace(last_plugin_backup=None)
    assert await common.upload_latest_plugin_backup_to_s3(db, server, user, ssh) == (True, "")

    s3.is_configured = lambda _owner: True
    ok, message = await common.upload_latest_plugin_backup_to_s3(db, server, user, ssh)
    assert ok is False
    assert "archive path" in message
    notify.queue_notify.assert_called_once()

    ssh.last_plugin_backup = {"path": "/tmp/plugins.tar.gz"}
    s3.upload_remote_backup.return_value = (True, "uploaded", "backups/1.tar.gz")
    ok, message = await common.upload_latest_plugin_backup_to_s3(db, server, user, ssh)
    assert (ok, message) == (True, "uploaded")
    assert notify.queue_notify.call_count == 2
    s3.upload_remote_backup.return_value = (False, "upload failed", None)
    assert await common.upload_latest_plugin_backup_to_s3(db, server, user, ssh) == (
        False,
        "upload failed",
    )

    owner = SimpleNamespace(id=2)
    db = _Session(server=server, owner=owner)
    server.user_id = 9
    s3.is_configured = lambda value: value is owner
    s3.upload_remote_backup.return_value = (True, "ok", None)
    assert await common.upload_latest_plugin_backup_to_s3(db, server, user, ssh) == (True, "ok")


def _patch_operation_modules(monkeypatch, *, final=None, enqueue=None, command=False):
    runner_name = (
        "api.routes.v1.operation_runner.enqueue_game_console_command"
        if command
        else "api.routes.v1.operation_runner.enqueue_server_operation"
    )
    runner_module = importlib.import_module("api.routes.v1.operation_runner")
    function_name = runner_name.rsplit(".", 1)[-1]
    enqueue_mock = AsyncMock(return_value=enqueue or {"operation_id": "op-1"})
    if isinstance(enqueue, BaseException):
        enqueue_mock.side_effect = enqueue
    monkeypatch.setattr(runner_module, function_name, enqueue_mock)
    hub_module = importlib.import_module("services.server_operation_hub")
    hub = SimpleNamespace(
        wait_until_terminal=AsyncMock(return_value=final or {"success": True, "message": "ok"})
    )
    monkeypatch.setattr(hub_module, "server_operation_hub", hub)
    return enqueue_mock, hub


@pytest.mark.asyncio
async def test_execute_single_server_action_success_missing_conflict_and_exception(monkeypatch):
    redis = _redis(monkeypatch)
    server = _server()
    session = _Session(server=server)
    monkeypatch.setattr(common, "async_session_maker", lambda: session)
    monkeypatch.setattr(common.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    lifecycle = Mock()
    monkeypatch.setattr(common, "apply_user_lifecycle_intent", lifecycle)
    enqueue, hub = _patch_operation_modules(monkeypatch)
    await common.execute_single_server_action(4, "start", 2, False, "batch")
    lifecycle.assert_called_once_with(server, "start")
    assert redis.set_batch_action_status.await_args.args[-2:] == ("success", "ok")
    assert hub.wait_until_terminal.await_count == 1
    assert enqueue.await_count == 1

    monkeypatch.setattr(common.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    await common.execute_single_server_action(4, "stop", 2, False, "missing")
    assert redis.set_batch_action_status.await_args.args[-1] == "Server not found"

    monkeypatch.setattr(common.Server, "get_by_id", AsyncMock(return_value=server))
    monkeypatch.setattr(common.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    conflict_module = importlib.import_module("services.server_operation_hub")
    conflict = conflict_module.ServerOperationConflict("already queued")
    _patch_operation_modules(monkeypatch, enqueue=conflict)
    await common.execute_single_server_action(4, "validate", 2, False, "conflict")
    assert redis.set_batch_action_status.await_args.args[-1] == "already queued"

    _patch_operation_modules(monkeypatch, enqueue=RuntimeError("runner"))
    await common.execute_single_server_action(4, "validate", 2, False, "error")
    assert redis.set_batch_action_status.await_args.args[-1] == "runner"

    monkeypatch.setattr(common.Server, "get_by_id", AsyncMock(return_value=server))
    await common.execute_single_server_action(4, "restart", 2, True, "admin")
    assert common.Server.get_by_id.await_count >= 1


@pytest.mark.asyncio
async def test_execute_single_server_plugins_covers_sequence_and_failures(monkeypatch):
    redis = _redis(monkeypatch)
    server = _server()
    session = _Session(server=server)
    monkeypatch.setattr(common, "async_session_maker", lambda: session)
    monkeypatch.setattr(common.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    runner_module = importlib.import_module("api.routes.v1.operation_runner")
    enqueue = AsyncMock(side_effect=[{"operation_id": "one"}, {"operation_id": "two"}])
    monkeypatch.setattr(runner_module, "enqueue_server_operation", enqueue)
    hub_module = importlib.import_module("services.server_operation_hub")
    hub = SimpleNamespace(
        wait_until_terminal=AsyncMock(
            side_effect=[{"success": True, "message": "ok"}, {"success": True, "message": "ok"}]
        )
    )
    monkeypatch.setattr(hub_module, "server_operation_hub", hub)
    await common.execute_single_server_plugins(
        4, ["metamod", "counterstrikesharp"], 2, False, "plugins"
    )
    assert redis.set_batch_action_status.await_args.args[-2] == "success"
    assert enqueue.await_count == 2

    await common.execute_single_server_plugins(4, ["unknown"], 2, False, "plugin-unknown")
    assert redis.set_batch_action_status.await_args.args[-2] == "failed"

    conflict = hub_module.ServerOperationConflict("busy")
    enqueue.side_effect = conflict
    await common.execute_single_server_plugins(4, ["metamod"], 2, False, "plugin-conflict")
    assert redis.set_batch_action_status.await_args.args[-2] == "failed"

    monkeypatch.setattr(common.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    await common.execute_single_server_plugins(4, ["metamod"], 2, False, "plugin-missing")
    assert redis.set_batch_action_status.await_args.args[-1] == "Server not found"

    monkeypatch.setattr(common.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    enqueue.side_effect = RuntimeError("unexpected")
    await common.execute_single_server_plugins(4, ["metamod"], 2, False, "plugin-error")
    assert redis.set_batch_action_status.await_args.args[-1] == "unexpected"


@pytest.mark.asyncio
async def test_execute_single_server_command_covers_authorization_conflict_and_result(monkeypatch):
    redis = _redis(monkeypatch)
    server = _server()
    session = _Session(server=server)
    monkeypatch.setattr(common, "async_session_maker", lambda: session)
    runner_module = importlib.import_module("api.routes.v1.operation_runner")
    enqueue = AsyncMock(return_value={"operation_id": "op-cmd"})
    monkeypatch.setattr(runner_module, "enqueue_game_console_command", enqueue)
    hub_module = importlib.import_module("services.server_operation_hub")
    monkeypatch.setattr(
        hub_module,
        "server_operation_hub",
        SimpleNamespace(
            wait_until_terminal=AsyncMock(return_value={"success": False, "message": "bad"})
        ),
    )
    await common.execute_single_server_command(4, "status", 2, False, "cmd")
    assert redis.set_batch_action_status.await_args.args[-2:] == ("failed", "bad")

    session.server = None
    await common.execute_single_server_command(4, "status", 2, False, "missing")
    assert redis.set_batch_action_status.await_args.args[-1] == "Server not found"
    session.server = _server(user_id=9)
    await common.execute_single_server_command(4, "status", 2, False, "denied")
    assert redis.set_batch_action_status.await_args.args[-1] == "Access denied"
    session.server = server
    enqueue.side_effect = hub_module.ServerOperationConflict("busy")
    await common.execute_single_server_command(4, "status", 2, False, "conflict")
    assert redis.set_batch_action_status.await_args.args[-1] == "busy"
    enqueue.side_effect = RuntimeError("command error")
    await common.execute_single_server_command(4, "status", 2, True, "error")
    assert redis.set_batch_action_status.await_args.args[-1] == "command error"
