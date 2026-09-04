"""覆盖定时任务计算、动作分派和任务清理路径。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import scheduled_task_service as scheduled


class _Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.scalar


class _Session:
    def __init__(self, server=None, task=None, rows=()):
        self.server = server
        self.task = task
        self.rows = rows
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return _Result(self.rows)

    async def get(self, model, _id):
        name = getattr(model, "__name__", "")
        return self.server if name == "Server" else self.task

    async def commit(self):
        self.commits += 1


def _task(**overrides):
    values = dict(
        id=1,
        server_id=3,
        name="task",
        action="update",
        schedule_type="interval",
        schedule_value="300",
        run_count=0,
        enabled=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _server(**overrides):
    values = dict(id=3, user_id=7, name="server", map_pool_sync_url=None)
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_schedule_calculations_and_lifecycle(monkeypatch):
    service = scheduled.ScheduledTaskService()
    now = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)
    assert service._calculate_daily_next_run(now, "15:00").hour == 15
    assert service._calculate_daily_next_run(now, "14:00").day == 7
    assert service._calculate_weekly_next_run(now, "MON:15:00").day == 6
    assert service._calculate_weekly_next_run(now, "SUN:08:00").weekday() == 6
    for value in ("bad", "25:00", "12:60"):
        with pytest.raises(ValueError):
            service._calculate_daily_next_run(now, value)
    for value in ("bad", "XXX:12:00", "MON:25:00"):
        with pytest.raises(ValueError):
            service._calculate_weekly_next_run(now, value)
    monkeypatch.setattr(scheduled, "get_current_time", lambda: now)
    assert service._calculate_next_run(_task(schedule_type="interval", schedule_value="300")) > now
    assert (
        service._calculate_next_run(
            _task(schedule_type="interval", schedule_value="1", action="map_pool_sync")
        )
        is None
    )
    assert service._calculate_next_run(_task(schedule_type="cron", schedule_value="*")) is None
    assert service._calculate_next_run(_task(schedule_type="unknown", schedule_value="x")) is None
    assert service._calculate_next_run(_task(schedule_type="daily", schedule_value="15:00"))
    assert service._calculate_next_run(_task(schedule_type="weekly", schedule_value="MON:15:00"))

    monkeypatch.setattr(service, "_calculate_all_next_runs", AsyncMock())
    monkeypatch.setattr(scheduled.asyncio, "sleep", AsyncMock())
    service.running = False
    await service.start()
    await service.stop()
    service.running_tasks[1] = asyncio.create_task(asyncio.sleep(10))
    service.running_server_ids.add(3)
    await service.stop()


@pytest.mark.asyncio
async def test_scheduled_actions_and_notifications(monkeypatch):
    service = scheduled.ScheduledTaskService()
    server = _server(should_skip_background_checks=lambda: False)
    progress = AsyncMock()
    ssh = SimpleNamespace(
        check_session_manager_available=AsyncMock(return_value=(True, "ready")),
        stop_server=AsyncMock(return_value=(True, "stopped")),
        start_server=AsyncMock(return_value=(True, "started")),
        update_server=AsyncMock(return_value=(True, "updated")),
        validate_server=AsyncMock(return_value=(True, "valid")),
        backup_plugins=AsyncMock(return_value=(False, "backup failed")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(scheduled.asyncio, "sleep", AsyncMock())
    assert await service._execute_restart(ssh, server, progress) == (True, "started")
    ssh.check_session_manager_available.return_value = (False, "tmux missing")
    assert "left untouched" in (await service._execute_restart(ssh, server, progress))[1]
    ssh.check_session_manager_available.return_value = (True, "ready")
    ssh.stop_server.return_value = (False, "busy")
    assert "shutdown failed" in (await service._execute_restart(ssh, server, progress))[1]

    for action in ("update", "validate", "stop", "backup_plugins", "unknown"):
        result = await service._execute_action(ssh, server, action)
        assert isinstance(result, tuple) and len(result) == 2
    server.map_pool_sync_url = None
    assert await service._execute_map_pool_sync(ssh, server) == (
        False,
        "No custom MapChooser map-pool URL is configured",
    )
    task = _task(action="update")
    monkeypatch.setattr(
        scheduled.discord_notification_service, "queue_notify", lambda *a, **k: None
    )
    await service._notify_task_result(server, task, True, "ok")
    await service._notify_task_result(server, _task(action="unknown"), False, "x")


@pytest.mark.asyncio
async def test_scheduled_check_update_and_non_hub_task(monkeypatch):
    service = scheduled.ScheduledTaskService()
    server = _server(should_skip_background_checks=lambda: False)
    task = _task(action="log_cleanup")
    session = _Session(server=server, task=task, rows=[task])
    monkeypatch.setattr(scheduled, "async_session_maker", lambda: session)
    monkeypatch.setattr(service, "_execute_task", AsyncMock())
    await service._check_and_execute_tasks()
    await asyncio.sleep(0)
    assert service._execute_task.await_count == 1
    monkeypatch.setattr(service, "_calculate_next_run", lambda _task: datetime.now(timezone.utc))
    await service._update_task_status(1, "success", None)
    await service.recalculate_next_run(1)

    class _SSH:
        async def connect(self, _server):
            return False, "offline"

        async def disconnect(self):
            return None

    monkeypatch.setattr(scheduled, "SSHManager", _SSH)

    class _Lock:
        async def acquire(self):
            return True

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(scheduled.maintenance_lock_service, "get", lambda *args, **kwargs: _Lock())
    monkeypatch.setattr(service, "_update_task_status", AsyncMock())
    monkeypatch.setattr(service, "_notify_task_result", AsyncMock())
    await service._execute_task(_task(action="log_cleanup"))
