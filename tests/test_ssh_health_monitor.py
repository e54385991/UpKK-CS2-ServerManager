"""Concurrency and resource-lifetime tests for SSH health monitoring."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from services.ssh_health_monitor import (
    MAX_CONCURRENT_HEALTH_CHECKS,
    SSHHealthMonitor,
)


@pytest.mark.asyncio
async def test_health_checks_release_db_and_use_bounded_concurrency(monkeypatch):
    servers = [SimpleNamespace(id=server_id) for server_id in range(8)]
    session_open = False

    class Scalars:
        @staticmethod
        def all():
            return servers

    class Result:
        @staticmethod
        def scalars():
            return Scalars()

    class Session:
        async def __aenter__(self):
            nonlocal session_open
            session_open = True
            return self

        async def __aexit__(self, *_args):
            nonlocal session_open
            session_open = False

        async def execute(self, _statement):
            assert session_open
            return Result()

    monkeypatch.setattr("modules.database.async_session_maker", Session)

    monitor = SSHHealthMonitor()
    monitor.last_check_times[999] = datetime.now()
    active = 0
    maximum_active = 0

    async def check_server(_server):
        nonlocal active, maximum_active
        assert session_open is False
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(monitor, "_check_server_health", check_server)

    await monitor._check_all_servers()

    assert 1 < maximum_active <= MAX_CONCURRENT_HEALTH_CHECKS
    assert monitor.last_check_times == {}
