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


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_source", ["child", "parent"])
async def test_health_check_cancellation_drains_active_and_queued_checks(
    monkeypatch, cancel_source
):
    servers = [SimpleNamespace(id=server_id) for server_id in range(8)]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: servers))

    monkeypatch.setattr("modules.database.async_session_maker", Session)
    monitor = SSHHealthMonitor()
    saturated = asyncio.Event()
    started = set()
    cleaned = set()

    async def check(server):
        started.add(server.id)
        if len(started) == MAX_CONCURRENT_HEALTH_CHECKS:
            saturated.set()
        try:
            await saturated.wait()
            if cancel_source == "child" and server.id == 0:
                raise asyncio.CancelledError
            await asyncio.Event().wait()
        finally:
            # Connection teardown can itself yield; cancellation must await it.
            await asyncio.sleep(0)
            cleaned.add(server.id)

    monkeypatch.setattr(monitor, "_check_server_health", check)
    existing_tasks = asyncio.all_tasks()
    batch = asyncio.create_task(monitor._check_all_servers())
    try:
        await asyncio.wait_for(saturated.wait(), timeout=1)
        if cancel_source == "parent":
            batch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(batch, timeout=1)
        assert len(started) >= MAX_CONCURRENT_HEALTH_CHECKS
        assert cleaned == started
        assert asyncio.all_tasks() <= existing_tasks
    finally:
        batch.cancel()
        await asyncio.gather(batch, return_exceptions=True)
