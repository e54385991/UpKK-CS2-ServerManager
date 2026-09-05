"""覆盖服务器相关 SQLModel 的纯属性和查询辅助方法。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from modules.models import (
    AuthType,
    CustomCommand,
    DeploymentLog,
    InitializedServer,
    MonitoringLog,
    ScheduledTask,
    Server,
    ServerStatus,
)


class _Scalars:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return list(self.rows)


class _Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return _Scalars(self.rows)


class _Session:
    def __init__(self, result):
        self.result = result
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return self.result


def _server(**overrides):
    values = dict(
        user_id=1,
        name="alpha",
        host="host",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        status=ServerStatus.PENDING,
    )
    values.update(overrides)
    return Server(**values)


def test_server_properties_status_and_background_age_policy(monkeypatch):
    server = _server(status=ServerStatus.RUNNING)
    assert repr(server).startswith("<Server(")
    assert server.is_password_auth and not server.is_key_auth
    assert server.is_running and not server.is_stopped
    assert not server.is_deploying and not server.is_error
    server.set_status(ServerStatus.ERROR)
    assert server.is_error
    server.auth_type = AuthType.KEY_FILE
    assert server.is_key_auth and not server.is_password_auth

    assert not server.should_skip_background_checks()
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    monkeypatch.setattr("modules.utils.get_current_time", lambda: now)
    server.is_ssh_down = True
    server.last_ssh_success = datetime(2026, 1, 5)
    assert server.should_skip_background_checks()
    server.last_ssh_success = now - timedelta(days=1)
    assert not server.should_skip_background_checks()
    server.last_ssh_success = None
    server.created_at = datetime(2026, 1, 5)
    assert server.should_skip_background_checks()
    server.created_at = None
    assert not server.should_skip_background_checks()


@pytest.mark.asyncio
async def test_server_query_helpers_cover_owner_and_admin_variants():
    row = _server(id=4)
    session = _Session(_Result([row], scalar=row))
    assert await Server.get_by_id_and_user(session, 4, 1) is row
    assert await Server.get_by_name_and_user(session, "alpha", 1) is row
    assert (
        await Server.get_by_host_directory_and_user(session, "host", "/home/cs2server/cs2", 1)
        is row
    )
    assert await Server.get_by_id(session, 4) is row
    assert await Server.get_by_api_key(session, "api") is row
    assert await Server.get_all_by_user(session, 1, skip=2, limit=3) == [row]
    assert await Server.get_all(session, skip=1, limit=2) == [row]
    assert await Server.get_all_with_panel_monitoring(session) == [row]
    assert await Server.get_all_with_auto_update(session) == [row]


@pytest.mark.asyncio
async def test_log_task_command_and_initialized_models_helpers():
    log = DeploymentLog(id=1, server_id=2, action="start", status="ok")
    monitor = MonitoringLog(id=2, server_id=2, event_type="status", status="ok", message="up")
    task = ScheduledTask(
        id=3,
        server_id=2,
        name="task",
        action="restart",
        schedule_type="interval",
        schedule_value="60",
        enabled=False,
        run_count=2,
        last_status="failed",
    )
    command = CustomCommand(id=4, user_id=1, server_id=2, name="say", commands="status")
    initialized = InitializedServer(
        id=5,
        user_id=1,
        name="saved",
        host="host",
        ssh_user="steam",
        ssh_password="pw",
    )
    assert "DeploymentLog" in repr(log)
    assert "MonitoringLog" in repr(monitor)
    assert not task.is_enabled and task.has_run and task.last_run_failed
    assert "CustomCommand" in repr(command)
    assert "InitializedServer" in repr(initialized)

    session = _Session(_Result([log], scalar=log))
    assert await DeploymentLog.get_logs_by_server(session, 2, skip=1, limit=4) == [log]
    session.result = _Result([task], scalar=task)
    assert await ScheduledTask.get_by_id_and_server(session, 3, 2) is task
    assert await ScheduledTask.get_all_by_server(session, 2) == [task]
    session.result = _Result([command], scalar=command)
    assert await CustomCommand.get_all_by_server_and_user(session, 2, 1) == [command]
    assert await CustomCommand.get_by_id_server_and_user(session, 4, 2, 1) is command
    session.result = _Result([initialized], scalar=initialized)
    assert await InitializedServer.get_all_by_user(session, 1) == [initialized]
