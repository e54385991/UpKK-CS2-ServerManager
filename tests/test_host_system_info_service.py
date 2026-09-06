"""Unit coverage for the low-frequency Linux host information cache."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.host_system_info_service import (
    HostSystemInfoService,
    parse_host_system_info,
)


def _probe_output() -> str:
    return "\n".join(
        [
            "SYSTEM_TYPE=Linux",
            "ARCHITECTURE=x86_64",
            "KERNEL_VERSION=6.8.0-31-generic",
            "CPU_MODEL=Intel(R) Xeon(R) Gold 6338 CPU",
            "CPU_CORES=16",
            "DISTRIBUTION=ubuntu",
            "DISTRIBUTION_VERSION=24.04",
            "DISTRIBUTION_PRETTY_NAME=Ubuntu 24.04.2 LTS",
            "MEMORY_TOTAL_BYTES=34359738368",
            "MEMORY_AVAILABLE_BYTES=17179869184",
            "PROBE_OK=1",
        ]
    )


def test_parse_host_system_info_returns_normalized_snapshot():
    result = parse_host_system_info(
        _probe_output(),
        server_id=7,
        collected_at="2026-09-04T13:00:00+08:00",
    )

    assert result["server_id"] == 7
    assert result["success"] is True
    assert result["cpu_model"] == "Intel(R) Xeon(R) Gold 6338 CPU"
    assert result["cpu_cores"] == 16
    assert result["distribution"] == "ubuntu"
    assert result["memory_available_bytes"] == 17179869184


@pytest.mark.asyncio
async def test_cache_hit_does_not_connect_to_server(monkeypatch):
    cached = parse_host_system_info(_probe_output(), server_id=7)

    class FakeRedis:
        async def get(self, _key):
            return cached

        async def acquire_lock(self, *_args):
            raise AssertionError("a valid cache hit must not acquire a lock")

    class UnexpectedSSH:
        def __init__(self):
            raise AssertionError("a valid cache hit must not create SSH manager")

    monkeypatch.setattr("services.host_system_info_service.redis_manager", FakeRedis())
    monkeypatch.setattr("services.host_system_info_service.SSHManager", UnexpectedSSH)

    result = await HostSystemInfoService().get_host_system_info(
        SimpleNamespace(id=7, host="host", ssh_port=22)
    )

    assert result["cached"] is True
    assert result["success"] is True


@pytest.mark.asyncio
async def test_cache_miss_collects_once_and_persists_snapshot(monkeypatch):
    stored: dict[str, object] = {}
    redis_reads = 0

    class FakeRedis:
        async def get(self, _key):
            nonlocal redis_reads
            redis_reads += 1
            return stored.get("value")

        async def acquire_lock(self, *_args):
            return True

        async def set(self, _key, value, expire):
            stored["value"] = value
            stored["expire"] = expire
            return True

        async def release_lock(self, *_args):
            stored["released"] = True
            return True

    class FakeSSH:
        async def connect(self, _server):
            return True, "connected"

        async def execute_command(self, _command, timeout):
            assert timeout == 15
            return True, _probe_output(), ""

        async def disconnect(self):
            stored["disconnected"] = True

    monkeypatch.setattr("services.host_system_info_service.redis_manager", FakeRedis())
    monkeypatch.setattr("services.host_system_info_service.SSHManager", FakeSSH)

    result = await HostSystemInfoService().get_host_system_info(
        SimpleNamespace(id=7, host="host", ssh_port=22)
    )

    assert result["cached"] is False
    assert result["success"] is True
    assert stored["expire"] == 15 * 60
    assert stored["released"] is True
    assert stored["disconnected"] is True
    assert redis_reads == 2
