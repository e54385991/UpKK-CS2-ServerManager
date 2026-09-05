"""补充定时任务服务的执行、外部依赖和错误清理分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import scheduled_task_service as scheduled


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, *, server=None, task=None, owner=None, rows=()):
        self.server = server
        self.task = task
        self.owner = owner
        self.rows = list(rows)
        self.commits = 0
        self.statements = []
        self.fail_execute = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, _item_id):
        name = getattr(model, "__name__", "")
        if name == "Server":
            return self.server
        if name == "User":
            return self.owner
        return self.task

    async def execute(self, statement):
        if self.fail_execute:
            raise RuntimeError("database unavailable")
        self.statements.append(statement)
        return _Result(self.rows)

    async def commit(self):
        self.commits += 1


class _Lock:
    def __init__(self, error=None):
        self.error = error
        self.exits = 0

    async def acquire(self):
        if self.error:
            raise self.error
        return True

    async def __aexit__(self, *_args):
        self.exits += 1
        return None


def _task(**overrides):
    values = {
        "id": 10,
        "server_id": 3,
        "name": "scheduled task",
        "action": "update",
        "schedule_type": "interval",
        "schedule_value": "300",
        "run_count": 0,
        "enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _server(**overrides):
    values = {
        "id": 3,
        "user_id": 7,
        "name": "server",
        "map_pool_sync_url": None,
        "should_skip_background_checks": lambda: False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ssh(**overrides):
    values = {
        "connect": AsyncMock(return_value=(True, "connected")),
        "disconnect": AsyncMock(),
        "start_server": AsyncMock(return_value=(True, "started")),
        "stop_server": AsyncMock(return_value=(True, "stopped")),
        "update_server": AsyncMock(return_value=(True, "updated")),
        "validate_server": AsyncMock(return_value=(True, "valid")),
        "check_session_manager_available": AsyncMock(return_value=(True, "ready")),
        "backup_plugins": AsyncMock(return_value=(True, "local backup")),
        "last_plugin_backup": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _session_patch(monkeypatch, db):
    monkeypatch.setattr(scheduled, "async_session_maker", lambda: db)


@pytest.mark.asyncio
async def test_check_loop_slots_duplicates_and_errors(monkeypatch):
    service = scheduled.ScheduledTaskService()
    first = _task(id=1, server_id=3)
    second = _task(id=2, server_id=4)
    third = _task(id=3, server_id=5)
    db = _Db(rows=[first, second, third])
    _session_patch(monkeypatch, db)
    running = SimpleNamespace(done=lambda: True)
    service.running_tasks[first.id] = running
    service.running_server_ids.add(second.server_id)
    monkeypatch.setattr(service, "_execute_task", AsyncMock())
    monkeypatch.setattr(scheduled, "MAX_CONCURRENT_SCHEDULED_TASKS", 1)
    await service._check_and_execute_tasks()
    assert service._execute_task.await_count == 0

    service.running_tasks.clear()
    service.running_server_ids.clear()
    await service._check_and_execute_tasks()
    await __import__("asyncio").sleep(0)
    assert service._execute_task.await_count == 2
    service._execute_task.side_effect = RuntimeError("not reached")
    db.fail_execute = True
    await service._check_and_execute_tasks()


@pytest.mark.asyncio
async def test_execution_loop_catches_check_error(monkeypatch):
    import asyncio

    service = scheduled.ScheduledTaskService()
    service.running = True
    check = AsyncMock(side_effect=RuntimeError("check failed"))
    monkeypatch.setattr(service, "_check_and_execute_tasks", check)
    monkeypatch.setattr(scheduled.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await service._execution_loop()
    check.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_task_server_and_precondition_skips(monkeypatch):
    service = scheduled.ScheduledTaskService()
    statuses = AsyncMock()
    monkeypatch.setattr(service, "_update_task_status", statuses)

    _session_patch(monkeypatch, _Db(server=None))
    await service._execute_task(_task(id=1))
    assert statuses.await_args_list[-1].args[1:] == ("failed", "Server 3 not found")

    blocked = _server()
    monkeypatch.setattr(scheduled, "automatic_start_block_reason", lambda _server: "maintenance")
    _session_patch(monkeypatch, _Db(server=blocked))
    await service._execute_task(_task(id=2, action="restart"))
    assert statuses.await_args_list[-1].args[1:] == ("skipped", "maintenance")

    stale = _server(should_skip_background_checks=lambda: True)
    monkeypatch.setattr(scheduled, "automatic_start_block_reason", lambda _server: None)
    _session_patch(monkeypatch, _Db(server=stale))
    await service._execute_task(_task(id=3, action="update"))
    assert statuses.await_args_list[-1].args[1:] == (
        "skipped",
        "Server marked as SSH down for 3+ consecutive days",
    )


@pytest.mark.asyncio
async def test_execute_task_hub_success_failure_conflict_and_cleanup(monkeypatch):
    service = scheduled.ScheduledTaskService()
    server = _server()
    statuses = AsyncMock()
    monkeypatch.setattr(service, "_update_task_status", statuses)

    import services.operation_enqueue as enqueue_module
    import services.server_operation_hub as hub_module

    enqueue_module.enqueue_server_operation = AsyncMock(return_value={"operation_id": "op-1"})
    hub_module.server_operation_hub.wait_until_terminal = AsyncMock(
        return_value={"success": True, "message": "done"}
    )
    _session_patch(monkeypatch, _Db(server=server))
    await service._execute_task(_task(action="update"))
    assert statuses.await_args_list[-1].args[1:] == ("success", None)

    hub_module.server_operation_hub.wait_until_terminal.return_value = {
        "success": False,
        "message": "failed remotely",
    }
    await service._execute_task(_task(id=11, action="stop"))
    assert statuses.await_args_list[-1].args[1:] == ("failed", "failed remotely")

    class Conflict(Exception):
        pass

    monkeypatch.setattr(hub_module, "ServerOperationConflict", Conflict)
    enqueue_module.enqueue_server_operation.side_effect = Conflict("already queued")
    await service._execute_task(_task(id=12, action="start"))
    assert statuses.await_args_list[-1].args[1:] == ("skipped", "already queued")
    assert service.running_tasks == {}
    assert service.running_server_ids == set()


@pytest.mark.asyncio
async def test_execute_task_non_hub_lock_connect_failure_and_action_failure(monkeypatch):
    service = scheduled.ScheduledTaskService()
    server = _server()
    task = _task(action="log_cleanup")
    statuses = AsyncMock()
    notified = AsyncMock()
    monkeypatch.setattr(service, "_update_task_status", statuses)
    monkeypatch.setattr(service, "_notify_task_result", notified)
    lock = _Lock()
    monkeypatch.setattr(scheduled.maintenance_lock_service, "get", lambda *_a, **_k: lock)
    _session_patch(monkeypatch, _Db(server=server))
    monkeypatch.setattr(
        scheduled, "SSHManager", lambda: _ssh(connect=AsyncMock(return_value=(False, "offline")))
    )
    await service._execute_task(task)
    assert statuses.await_args_list[-1].args[1] == "failed"
    notified.assert_awaited_once()
    assert lock.exits == 1

    lock = _Lock()
    monkeypatch.setattr(scheduled.maintenance_lock_service, "get", lambda *_a, **_k: lock)
    ssh = _ssh()
    monkeypatch.setattr(scheduled, "SSHManager", lambda: ssh)
    monkeypatch.setattr(
        service, "_execute_action", AsyncMock(return_value=(False, "action failed"))
    )
    await service._execute_task(_task(id=11, action="log_cleanup"))
    assert statuses.await_args_list[-1].args[1:] == ("failed", "action failed")
    assert ssh.disconnect.await_count == 1
    assert lock.exits == 1

    failing_lock = _Lock(scheduled.OperationBusyError("busy"))
    monkeypatch.setattr(scheduled.maintenance_lock_service, "get", lambda *_a, **_k: failing_lock)
    await service._execute_task(_task(id=12, action="log_cleanup"))
    assert statuses.await_args_list[-1].args[1:] == ("skipped", "busy")


@pytest.mark.asyncio
async def test_execute_task_second_server_lookup_and_unexpected_error(monkeypatch):
    service = scheduled.ScheduledTaskService()
    statuses = AsyncMock()
    notified = AsyncMock()
    monkeypatch.setattr(service, "_update_task_status", statuses)
    monkeypatch.setattr(service, "_notify_task_result", notified)
    lock = _Lock()
    monkeypatch.setattr(scheduled.maintenance_lock_service, "get", lambda *_a, **_k: lock)

    class _ChangingDb(_Db):
        def __init__(self):
            super().__init__(server=_server())
            self.lookups = 0

        async def get(self, model, item_id):
            if getattr(model, "__name__", "") == "Server":
                self.lookups += 1
                return self.server if self.lookups == 1 else None
            return await super().get(model, item_id)

    _session_patch(monkeypatch, _ChangingDb())
    await service._execute_task(_task(id=20, action="log_cleanup"))
    assert statuses.await_args_list[-1].args[1:] == ("failed", "Server 3 not found")

    _session_patch(monkeypatch, _Db(server=_server()))
    monkeypatch.setattr(scheduled, "SSHManager", lambda: _ssh())
    monkeypatch.setattr(service, "_execute_action", AsyncMock(side_effect=RuntimeError("boom")))
    await service._execute_task(_task(id=21, action="log_cleanup"))
    assert statuses.await_args_list[-1].args[1:] == ("failed", "boom")
    assert notified.await_count == 1


@pytest.mark.asyncio
async def test_backup_plugins_s3_branches(monkeypatch):
    service = scheduled.ScheduledTaskService()
    server = _server()
    progress = AsyncMock()
    owner = SimpleNamespace(id=7)
    db = _Db(owner=owner)
    _session_patch(monkeypatch, db)
    ssh = _ssh()
    monkeypatch.setattr(scheduled.s3_backup_service, "is_configured", lambda _owner: False)
    assert await service._execute_backup_plugins(ssh, server, progress) == (True, "local backup")

    monkeypatch.setattr(scheduled.s3_backup_service, "is_configured", lambda _owner: True)
    monkeypatch.setattr(
        scheduled.discord_notification_service, "queue_notify", lambda *_a, **_k: None
    )
    assert await service._execute_backup_plugins(ssh, server, progress) == (
        False,
        "Plugin backup completed locally, but the archive path was not captured for S3 upload.",
    )

    ssh.last_plugin_backup = {"path": "/tmp/plugins.tar.gz"}
    upload = AsyncMock(return_value=(False, "S3 down", "backup/key"))
    monkeypatch.setattr(scheduled.s3_backup_service, "upload_remote_backup", upload)
    failed = await service._execute_backup_plugins(ssh, server, progress)
    assert failed == (False, "local backup\nS3 down")
    upload.return_value = (True, "uploaded", "backup/key")
    assert await service._execute_backup_plugins(ssh, server, progress) == (
        True,
        "local backup\nuploaded",
    )

    ssh.backup_plugins.return_value = (False, "backup failed")
    assert await service._execute_backup_plugins(ssh, server, progress) == (
        False,
        "backup failed",
    )
    db.owner = None
    ssh.backup_plugins.return_value = (True, "local backup")
    assert await service._execute_backup_plugins(ssh, server, progress) == (True, "local backup")


@pytest.mark.asyncio
async def test_map_pool_sync_and_execute_action_dispatch(monkeypatch):
    service = scheduled.ScheduledTaskService()
    server = _server(map_pool_sync_url="https://maps.example/pool")
    ssh = _ssh()
    import services.remote_map_pool_service as pool_module

    monkeypatch.setattr(
        pool_module, "synchronize_remote_map_pool", AsyncMock(return_value=("x", 4))
    )
    assert await service._execute_map_pool_sync(ssh, server) == (
        True,
        "Synchronized 4 maps from the custom remote map pool",
    )
    monkeypatch.setattr(
        pool_module,
        "synchronize_remote_map_pool",
        AsyncMock(side_effect=pool_module.RemoteMapPoolError("invalid pool")),
    )
    assert await service._execute_map_pool_sync(ssh, server) == (False, "invalid pool")

    monkeypatch.setattr(scheduled, "automatic_start_block_reason", lambda _server: None)
    monkeypatch.setattr(
        pool_module,
        "synchronize_remote_map_pool",
        AsyncMock(return_value=("x", 4)),
    )
    for action, method in (
        ("start", "start_server"),
        ("stop", "stop_server"),
        ("update", "update_server"),
        ("validate", "validate_server"),
    ):
        result = await service._execute_action(ssh, server, action)
        assert result[0] is True
        getattr(ssh, method).assert_awaited()
    assert await service._execute_action(ssh, server, "restart") == (True, "started")
    assert await service._execute_action(ssh, server, "map_pool_sync") == (
        True,
        "Synchronized 4 maps from the custom remote map pool",
    )
    cleanup = AsyncMock(return_value=(True, "cleaned"))
    import services.system_cleanup_service as cleanup_module

    monkeypatch.setattr(cleanup_module.system_cleanup_service, "run_scheduled", cleanup)
    assert await service._execute_action(ssh, server, "log_cleanup") == (True, "cleaned")
    monkeypatch.setattr(scheduled, "automatic_start_block_reason", lambda _server: "blocked")
    assert await service._execute_action(ssh, server, "start") == (False, "blocked")
    assert await service._execute_action(ssh, server, "unknown") == (
        False,
        "Unknown action: unknown",
    )
    monkeypatch.setattr(scheduled, "automatic_start_block_reason", lambda _server: None)
    ssh.start_server.side_effect = RuntimeError("start error")
    assert await service._execute_action(ssh, server, "start") == (False, "start error")


@pytest.mark.asyncio
async def test_status_and_next_run_database_error_paths(monkeypatch):
    service = scheduled.ScheduledTaskService()
    task = _task()
    db = _Db(task=None)
    _session_patch(monkeypatch, db)
    await service._update_task_status(task.id, "failed", "missing")
    assert db.commits == 0
    db.task = task
    monkeypatch.setattr(service, "_calculate_next_run", lambda _task: None)
    await service._update_task_status(task.id, "success", None)
    assert db.commits == 1
    db.fail_execute = True
    await service._update_task_status(task.id, "failed", "db error")
    await service.recalculate_next_run(task.id)

    all_db = _Db(rows=[task, _task(id=11, schedule_type="unknown", schedule_value="x")])
    _session_patch(monkeypatch, all_db)
    await service._calculate_all_next_runs()
    assert all_db.commits == 1
    failing = _Db(rows=[])
    failing.fail_execute = True
    _session_patch(monkeypatch, failing)
    await service._calculate_all_next_runs()
    await service.recalculate_next_run(999)
