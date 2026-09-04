"""覆盖 Redis 管理器在连接异常、数据损坏和一致性失败时的降级行为。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from modules.config import settings
from services.redis_manager import RedisManager


class _Pipeline:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def set(self, *_args, **_kwargs):
        return None

    def setex(self, *_args, **_kwargs):
        return None

    def rpush(self, *_args, **_kwargs):
        return None

    def ltrim(self, *_args, **_kwargs):
        return None

    def expire(self, *_args, **_kwargs):
        return None

    async def execute(self):
        return []


class _Redis:
    def __init__(self):
        self.pipeline_obj = _Pipeline()

    async def set(self, *_args, **_kwargs):
        return True

    async def setex(self, *_args, **_kwargs):
        return True

    async def get(self, _key):
        return None

    async def delete(self, *_keys):
        return 1

    async def lrange(self, *_args):
        return []

    async def lpush(self, *_args):
        return 1

    async def rpush(self, *_args):
        return 1

    async def expire(self, *_args):
        return True

    async def lrem(self, *_args):
        return 1

    async def scan(self, *_args, **_kwargs):
        return 0, []

    def pipeline(self, **_kwargs):
        return self.pipeline_obj


def _manager(monkeypatch):
    manager = RedisManager.__new__(RedisManager)
    manager.client = _Redis()
    manager._coordination_retry_after = 0
    monkeypatch.setattr(settings, "REDIS_KEY_PREFIX", "test")
    return manager


@pytest.mark.asyncio
async def test_redis_set_expiry_fallback_and_failure_paths(monkeypatch):
    manager = _manager(monkeypatch)
    manager.client.set = AsyncMock(side_effect=TypeError("new API unavailable"))
    manager.client.setex = AsyncMock(return_value=True)
    assert await manager._set_with_expiry("test:key", "value", 5) is True
    manager.client.setex = AsyncMock(side_effect=RuntimeError("legacy API unavailable"))
    assert await manager.set("key", {"x": 1}) is False

    manager.client.scan = AsyncMock(side_effect=RuntimeError("scan down"))
    assert await manager.clear_server_cache(4) is False
    manager.client.scan = AsyncMock(return_value=(0, []))
    assert await manager.delete_by_pattern("server:4:*") == 0


@pytest.mark.asyncio
async def test_redis_initialized_server_consistency_and_read_failures(monkeypatch):
    manager = _manager(monkeypatch)
    manager.set = AsyncMock(return_value=False)
    with pytest.raises(Exception, match="store server data"):
        await manager.set_initialized_server(1, {"host": "x"})

    manager.set = AsyncMock(return_value=True)
    manager.client.rpush = AsyncMock(side_effect=RuntimeError("list unavailable"))
    manager.delete = AsyncMock(return_value=True)
    with pytest.raises(Exception, match="update server list"):
        await manager.set_initialized_server(1, {"host": "x"})
    manager.client.lrange = AsyncMock(side_effect=RuntimeError("list unavailable"))
    assert await manager.get_initialized_servers(1) == []
    manager.client.lrem = AsyncMock(side_effect=RuntimeError("remove unavailable"))
    assert await manager.delete_initialized_server(1, "key") is True


@pytest.mark.asyncio
async def test_redis_batch_and_monitoring_corrupt_or_unavailable_paths(monkeypatch):
    manager = _manager(monkeypatch)
    manager._set_with_expiry = AsyncMock(side_effect=RuntimeError("set down"))
    assert await manager.set_batch_action_status("batch", 1, "pending") is False
    manager.client.pipeline_obj.execute = AsyncMock(side_effect=RuntimeError("pipeline down"))
    assert await manager.set_batch_action_statuses("batch", [1], "pending") is False

    manager.client.scan = AsyncMock(return_value=(0, [b"test:batch_action:b:1"]))
    manager.client.mget = AsyncMock(return_value=["not-json"])
    assert await manager.get_batch_action_status("b") == {"1": "not-json"}
    manager.client.scan = AsyncMock(side_effect=RuntimeError("scan down"))
    assert await manager.get_batch_action_status("b") == {}

    manager.client.get = AsyncMock(return_value="[]")
    assert await manager.get_batch_action_meta("b") is None
    manager.client.get = AsyncMock(side_effect=RuntimeError("get down"))
    assert await manager.get_batch_action_meta("b") is None
    manager._set_with_expiry = AsyncMock(side_effect=RuntimeError("set down"))
    assert await manager.set_batch_action_meta("b", actor_user_id=1, action="start") is False

    manager.client.lpush = AsyncMock(side_effect=RuntimeError("push down"))
    assert await manager.append_monitoring_log(1, "status_check", "failed", "down") is False
    manager.client.lrange = AsyncMock(side_effect=RuntimeError("read down"))
    assert await manager.get_monitoring_logs(1, "status_check") == []
    assert await manager.get_monitoring_logs(1) == []
    manager.client.delete = AsyncMock(side_effect=RuntimeError("delete down"))
    assert await manager.clear_monitoring_logs(1, "status_check") is False
    assert await manager.clear_monitoring_logs(1) is False
