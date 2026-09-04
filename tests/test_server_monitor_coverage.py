"""覆盖服务器监控的重启保护、A2S 轮询和后台任务清理。"""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.models import ServerStatus
from services import server_monitor as monitor_module


class _Session:
    def __init__(self, server):
        self.server = server
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _id):
        return self.server

    async def commit(self):
        self.commits += 1


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _server(**overrides):
    values = dict(
        id=3,
        enable_panel_monitoring=True,
        enable_a2s_monitoring=True,
        a2s_check_interval_seconds=1,
        monitor_interval_seconds=1,
        a2s_query_host=None,
        a2s_query_port=None,
        host="host",
        game_port=27015,
        a2s_failure_threshold=3,
        auto_restart_on_crash=False,
        discord_crash_restart_min_interval_minutes=10,
        status=ServerStatus.STOPPED,
        manual_stop_requested=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_restart_history_and_notification(monkeypatch):
    service = monitor_module.ServerMonitor()
    assert service.can_restart(3)[0]
    for _ in range(5):
        service.record_restart(3)
    allowed, reason = service.can_restart(3)
    assert not allowed and "disabled" in reason
    info = service.get_restart_info(3)
    assert info["restart_count"] == 5 and not info["can_restart"]
    service.a2s_failure_count[3] = 2
    service.reset_restart_history(3)
    assert service.get_restart_info(99)["restart_count"] == 0
    server = _server(auto_restart_on_crash=True)
    monkeypatch.setattr(
        monitor_module.discord_notification_service, "queue_notify", lambda *a, **k: True
    )
    assert service.queue_restart_notification(
        server, success=True, title="ok", message="done", trigger="test"
    )


@pytest.mark.asyncio
async def test_guarded_restart_policy_and_monitor_one_pass(monkeypatch):
    service = monitor_module.ServerMonitor()
    server = _server(auto_restart_on_crash=True)
    from modules import database
    from services.plugins import diagnostic_policy

    monkeypatch.setattr(database, "async_session_maker", lambda: _Session(server))
    monkeypatch.setattr(diagnostic_policy, "has_diagnostic_blocker", AsyncMock(return_value=False))
    monkeypatch.setattr(monitor_module.maintenance_lock_service, "get", lambda *a, **k: _Lock())
    ssh = SimpleNamespace(
        check_session_manager_available=AsyncMock(return_value=(True, "ready")),
        stop_server=AsyncMock(return_value=(True, "stopped")),
        start_server=AsyncMock(return_value=(True, "started")),
    )
    success, message, current = await service._perform_guarded_restart(3, ssh)
    assert success and message == "started" and current is server
    server.enable_panel_monitoring = False
    result = await service._perform_guarded_restart(3, ssh)
    assert result[0] is None and "disabled" in result[1]
    server.enable_panel_monitoring = True
    server.auto_restart_on_crash = True
    monkeypatch.setattr(diagnostic_policy, "has_diagnostic_blocker", AsyncMock(return_value=True))
    assert "quarantine" in (await service._perform_guarded_restart(3, ssh))[1]

    # A2S success followed by a missing DB row exercises the normal update and
    # monitor shutdown paths without opening SSH or Redis.
    server.auto_restart_on_crash = False
    server.enable_panel_monitoring = True
    sessions = iter([_Session(server), _Session(None)])
    monkeypatch.setattr(database, "async_session_maker", lambda: next(sessions))
    from services import a2s_query

    redis_module = importlib.import_module("services.redis_manager")

    monkeypatch.setattr(
        a2s_query, "a2s_service", SimpleNamespace(check_server_health=AsyncMock(return_value=True))
    )
    redis_module.redis_manager.append_monitoring_log = AsyncMock(return_value=True)
    monkeypatch.setattr(monitor_module.asyncio, "sleep", AsyncMock())
    await service.monitor_server(3, ssh)
    assert redis_module.redis_manager.append_monitoring_log.await_count >= 2


@pytest.mark.asyncio
async def test_monitor_task_start_stop_and_ssh_health_updates(monkeypatch):
    service = monitor_module.ServerMonitor()
    monkeypatch.setattr(service, "monitor_server", AsyncMock())
    service.start_monitoring(1, SimpleNamespace())
    service.start_monitoring(1, SimpleNamespace())
    await asyncio.sleep(0)
    service.stop_monitoring(1)
    await service.stop_all()
    assert not service.monitoring_tasks


@pytest.mark.asyncio
async def test_process_monitor_down_without_restart_and_redis_failure(monkeypatch):
    service = monitor_module.ServerMonitor()
    server = _server(enable_a2s_monitoring=False, auto_restart_on_crash=False)
    sessions = iter([_Session(server), _Session(server), _Session(None)])
    from modules import database

    monkeypatch.setattr(database, "async_session_maker", lambda: next(sessions))
    redis = importlib.import_module("services.redis_manager").redis_manager
    redis.append_monitoring_log = AsyncMock(side_effect=RuntimeError("redis down"))
    ssh = SimpleNamespace(get_server_status=AsyncMock(return_value=(True, "stopped")))
    monkeypatch.setattr(monitor_module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(service, "queue_restart_notification", lambda *a, **k: True)
    await service.monitor_server(server.id, ssh)
    assert ssh.get_server_status.await_count == 1


@pytest.mark.asyncio
async def test_a2s_threshold_restart_failure_and_blocked_paths(monkeypatch):
    service = monitor_module.ServerMonitor()
    server = _server(auto_restart_on_crash=True, a2s_failure_threshold=1)
    updated = _server(auto_restart_on_crash=True, a2s_failure_threshold=1)
    missing = _Session(None)
    sessions = iter([_Session(server), _Session(updated), missing])
    from modules import database
    from services import a2s_query

    monkeypatch.setattr(database, "async_session_maker", lambda: next(sessions))
    redis = importlib.import_module("services.redis_manager").redis_manager
    redis.append_monitoring_log = AsyncMock(return_value=True)
    monkeypatch.setattr(
        a2s_query,
        "a2s_service",
        SimpleNamespace(check_server_health=AsyncMock(return_value=False)),
    )
    monkeypatch.setattr(service, "_perform_guarded_restart", AsyncMock(return_value=(False, "start failed", server)))
    notifications = []
    monkeypatch.setattr(
        service,
        "queue_restart_notification",
        lambda *args, **kwargs: notifications.append(kwargs) or True,
    )
    monkeypatch.setattr(monitor_module.asyncio, "sleep", AsyncMock())
    await service.monitor_server(server.id, SimpleNamespace())
    assert notifications and notifications[-1]["success"] is False

    service = monitor_module.ServerMonitor()
    server = _server(auto_restart_on_crash=True, a2s_failure_threshold=1)
    sessions = iter([_Session(server), _Session(server), _Session(None)])
    monkeypatch.setattr(database, "async_session_maker", lambda: next(sessions))
    monkeypatch.setattr(service, "can_restart", lambda _id: (False, "loop protected"))
    monkeypatch.setattr(service, "queue_restart_notification", lambda *a, **k: True)
    await service.monitor_server(server.id, SimpleNamespace())
    assert server.status == ServerStatus.ERROR
