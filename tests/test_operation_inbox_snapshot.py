"""Batch inbox snapshot: one memory scan, chunked Redis, event tails only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from services.operations.inbox_messages import REDIS_BATCH
from services.operations.inbox_snapshot import collect_hub_snapshot
from services.server_operation_hub import ServerOperationHub


def _slice(items: list, start: int, end: int) -> list:
    length = len(items)
    if start < 0:
        start = length + start
    if end < 0:
        end = length + end
    start = max(0, start)
    end = min(length - 1, end)
    if length == 0 or start > end:
        return []
    return items[start : end + 1]


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self.redis = redis
        self.ops: list[tuple[str, int, int]] = []

    def lrange(self, key, start, end):
        self.redis.lrange_calls.append((key, start, end))
        self.ops.append((key, start, end))

    async def execute(self):
        return [_slice(self.redis.lists.get(key, []), start, end) for key, start, end in self.ops]


class FakeRedis:
    def __init__(self):
        self.store: dict[str, object] = {}
        self.lists: dict[str, list] = {}
        self.get_many_sizes: list[int] = []
        self.lrange_calls: list[tuple[str, int, int]] = []
        self.get = AsyncMock(side_effect=self._get)
        self.set = AsyncMock()
        self.client = self

    def prefixed_key(self, key: str) -> str:
        return "p:" + key

    async def _get(self, key: str):
        return self.store.get(key)

    async def get_many(self, keys):
        self.get_many_sizes.append(len(keys))
        return [self.store.get(key) for key in keys]

    def pipeline(self, transaction=False):
        return FakePipeline(self)


class CountingRecords(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scans = 0

    def values(self):
        self.scans += 1
        return super().values()


class GuardedEvents(list):
    def __iter__(self):
        raise AssertionError("inbox snapshot must not copy or scan full event history")

    def copy(self):
        raise AssertionError("inbox snapshot must not copy event history")


def _record(operation_id, *, status="queued", server_id=1, **extra):
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "operation_id": operation_id,
        "server_id": server_id,
        "action": "install_plugin",
        "command": "plugin-market install 1",
        "status": status,
        "success": status == "completed",
        "message": extra.pop("message", None),
        "server_status": None,
        "actor_user_id": 1,
        "started_at": extra.pop("started_at", now),
        "completed_at": extra.pop(
            "completed_at", now if status in {"failed", "completed"} else None
        ),
    }
    payload.update(extra)
    return payload


@pytest.fixture
def hub(monkeypatch):
    instance = ServerOperationHub()
    redis = FakeRedis()
    monkeypatch.setattr("services.server_operation_hub.redis_manager", redis)
    instance._persist_failed = AsyncMock()
    instance._persist_completed = AsyncMock()
    instance._records = CountingRecords()
    return instance, redis


@pytest.mark.asyncio
async def test_snapshot_scans_memory_once_for_many_servers(hub):
    instance, _redis = hub
    for server_id in (1, 2, 3):
        operation_id = f"op-{server_id}"
        instance._records[operation_id] = _record(
            operation_id, server_id=server_id, status="running"
        )
        instance._current[server_id] = operation_id
        instance._pending[server_id] = []
        instance._failed[server_id] = []
        instance._completed[server_id] = []
    snapshot = await collect_hub_snapshot(instance, [1, 2, 3])
    assert snapshot.memory_scans == 1
    assert instance._records.scans == 1
    assert len(snapshot.slices) == 3


@pytest.mark.asyncio
async def test_snapshot_redis_batches_stay_within_limit(hub):
    instance, redis = hub
    server_ids = list(range(1, 502))
    snapshot = await collect_hub_snapshot(instance, server_ids)
    assert snapshot.memory_scans == 1
    assert redis.get_many_sizes
    assert all(size <= REDIS_BATCH for size in redis.get_many_sizes)
    assert len(redis.get_many_sizes) >= 2


@pytest.mark.asyncio
async def test_snapshot_reads_event_tail_not_full_history(hub):
    instance, redis = hub
    record = _record("live", status="running")
    instance._records["live"] = record
    instance._current[1] = "live"
    instance._pending[1] = []
    instance._failed[1] = []
    instance._completed[1] = []
    instance._events["live"] = GuardedEvents([{"message": "Extracting archive"}])
    snapshot = await collect_hub_snapshot(instance, [1])
    assert snapshot.slices[1].active[0].latest_message == "Extracting archive"
    assert redis.lrange_calls == []


@pytest.mark.asyncio
async def test_snapshot_corrupt_tail_uses_bounded_reverse_window(hub):
    instance, redis = hub
    record = _record("live", status="running", message="fallback")
    instance._records["live"] = record
    instance._current[1] = "live"
    instance._pending[1] = []
    instance._failed[1] = []
    instance._completed[1] = []
    key = redis.prefixed_key(instance._events_key("live"))
    redis.lists[key] = [
        json.dumps({"message": "still running"}),
        "{not-json",
    ]
    snapshot = await collect_hub_snapshot(instance, [1])
    assert snapshot.slices[1].active[0].latest_message == "still running"
    assert all(start != 0 or end != -1 for _key, start, end in redis.lrange_calls)
    assert any(start == -1 and end == -1 for _key, start, end in redis.lrange_calls)
    assert any(start == -16 and end == -1 for _key, start, end in redis.lrange_calls)


@pytest.mark.asyncio
async def test_snapshot_falls_back_to_record_message(hub):
    instance, _redis = hub
    record = _record("live", status="running", message="Queued worker")
    instance._records["live"] = record
    instance._current[1] = "live"
    instance._pending[1] = []
    instance._failed[1] = []
    instance._completed[1] = []
    snapshot = await collect_hub_snapshot(instance, [1])
    assert snapshot.slices[1].active[0].latest_message == "Queued worker"


@pytest.mark.asyncio
async def test_snapshot_queue_position_counts_non_active_current(hub):
    instance, _redis = hub
    done = _record("done", status="completed", server_id=1)
    queued = _record("wait", status="queued", server_id=1)
    instance._records.update({"done": done, "wait": queued})
    instance._current[1] = "done"
    instance._pending[1] = ["wait"]
    instance._failed[1] = []
    instance._completed[1] = []
    snapshot = await collect_hub_snapshot(instance, [1])
    assert [row.record["operation_id"] for row in snapshot.slices[1].active] == ["wait"]
    assert snapshot.slices[1].active[0].queue_position == 1


@pytest.mark.asyncio
async def test_snapshot_prune_keeps_ids_added_during_read(hub):
    instance, redis = hub
    expired_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    old = _record("old", status="failed", completed_at=expired_at)
    instance._records["old"] = old
    instance._current[1] = "gone"
    instance._pending[1] = []
    instance._failed[1] = ["old"]
    instance._completed[1] = []
    redis.store[instance._failed_key(1)] = ["old", "new"]
    snapshot = await collect_hub_snapshot(instance, [1])
    assert snapshot.slices[1].failed == []
    assert instance._failed[1] == ["new"]
    instance._persist_failed.assert_awaited()
