"""覆盖服务器操作 FIFO、持久化回退和 SSE 重放的边界分支。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import server_operation_hub as module


class _Pipeline:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    def rpush(self, *args):
        self.calls.append(("rpush", args))

    def ltrim(self, *args):
        self.calls.append(("ltrim", args))

    def expire(self, *args):
        self.calls.append(("expire", args))

    async def execute(self):
        if self.error:
            raise self.error


class _Redis:
    def __init__(self, value=None):
        self.value = value
        self.get = AsyncMock(return_value=value)
        self.set = AsyncMock()
        self.delete = AsyncMock()
        self.client = SimpleNamespace(
            lrange=AsyncMock(return_value=[]),
            expire=AsyncMock(),
            pipeline=lambda **_kwargs: _Pipeline(),
        )

    def prefixed_key(self, key):
        return "prefix:" + key


class _Task:
    def __init__(self, done=False):
        self.done_value = done
        self.cancelled = False

    def done(self):
        return self.done_value

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def hub(monkeypatch):
    instance = module.ServerOperationHub()
    redis = _Redis()
    monkeypatch.setattr(module, "redis_manager", redis)
    monkeypatch.setattr(instance, "_persist_record", AsyncMock())
    monkeypatch.setattr(instance, "_persist_event", AsyncMock())
    monkeypatch.setattr(instance, "_persist_pending", AsyncMock())
    monkeypatch.setattr(instance, "_persist_failed", AsyncMock())
    monkeypatch.setattr(instance, "_expire_events", AsyncMock())
    monkeypatch.setattr(instance, "_forget_operation", AsyncMock())
    return instance


def _record(operation_id="op", *, status="queued", server_id=1, **extra):
    return {
        "operation_id": operation_id,
        "server_id": server_id,
        "action": "deploy",
        "status": status,
        "message": None,
        "started_at": "2026-01-01T00:00:00+00:00",
        "completed_at": None,
        **extra,
    }


def test_datetime_and_record_helpers():
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert module._as_datetime(aware) == aware
    assert module._as_datetime("2026-01-01T00:00:00Z").tzinfo == timezone.utc
    assert module._as_datetime("bad") is None
    assert module._as_datetime(None) is None
    assert module._trim_events([{"n": i} for i in range(4)])[-1]["n"] == 3
    assert len(module._trim_events([{"n": 1}])) == 1
    assert module._record_ttl({"status": "failed"}) == module.FAILED_RETENTION_SECONDS
    assert module._record_ttl({"status": "completed"}) == module.OPERATION_TTL_SECONDS
    conflict = module.ServerOperationConflict("busy", "op")
    assert conflict.operation_id == "op"


@pytest.mark.asyncio
async def test_create_get_schedule_and_queue_limit(hub, monkeypatch):
    first = await hub.create(
        server_id=1, action="deploy", actor_user_id=4, command="deploy", extra={"target_path": "/x"}
    )
    assert first["target_path"] == "/x"
    assert (await hub.get(first["operation_id"]))["action"] == "deploy"
    stored = _record("stored", status="completed")
    hub._records.clear()
    monkeypatch.setattr(module.redis_manager, "get", AsyncMock(side_effect=[stored, None]))
    assert (await hub.get("stored"))["status"] == "completed"
    assert await hub.get("missing") is None

    hub._records[first["operation_id"]] = first
    hub._current[1] = first["operation_id"]
    hub._records[first["operation_id"]]["status"] = "running"
    hub._pending[1] = ["a"]
    second = await hub.create(server_id=1, action="queued", actor_user_id=4)
    assert second["status"] == "queued"
    hub._pending[1] = [str(i) for i in range(module.MAX_PENDING_PER_SERVER)]
    with pytest.raises(module.ServerOperationConflict):
        await hub.create(server_id=1, action="overflow", actor_user_id=4)

    module.redis_manager.get = AsyncMock(return_value=None)
    hub._current[2] = "none"
    hub._runners["none"] = lambda: None
    hub._start = lambda *_args, **_kwargs: None
    await hub.schedule("does-not-exist", lambda: None)
    hub._records["none"] = _record("none", server_id=2)
    await hub.schedule("none", lambda: None)
    await hub.mark_running("none")
    assert hub._records["none"]["status"] == "running"


@pytest.mark.asyncio
async def test_listing_failed_dismiss_clear_and_latest_message(hub, monkeypatch):
    old = _record(
        "old",
        status="failed",
        server_id=1,
        completed_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
    )
    fresh = _record(
        "fresh", status="failed", server_id=1, completed_at=datetime.now(timezone.utc).isoformat()
    )
    hub._records.update({"old": old, "fresh": fresh})
    hub._failed[1] = ["old", "fresh", "missing"]
    failed = await hub.list_failed_for_server(1)
    assert [item["operation_id"] for item in failed] == ["fresh"]
    assert hub._failed[1] == ["fresh"]
    assert await hub.dismiss_failed("fresh") == fresh
    assert await hub.dismiss_failed("missing") is None

    hub._records["fresh"]["message"] = "fallback"
    hub._events["fresh"] = []
    assert await hub.latest_message("fresh") == "fallback"
    hub._events["fresh"] = [{"message": "latest"}]
    assert await hub.latest_message("fresh") == "latest"
    assert await hub.clear_failed([1, 2]) == 0

    current = _record("current", status="failed", server_id=3)
    hub._records["current"] = current
    hub._current[3] = "current"
    hub._failed[3] = ["current"]
    await hub.dismiss_failed("current")
    assert 3 not in hub._current
    monkeypatch.setattr(
        module.redis_manager, "delete", AsyncMock(side_effect=RuntimeError("redis"))
    )
    await hub.dismiss_failed("nonexistent")


@pytest.mark.asyncio
async def test_abort_finish_promote_and_forward_progress(hub, monkeypatch):
    current = _record("current", status="running", server_id=1)
    next_record = _record("next", status="queued", server_id=1)
    hub._records.update({"current": current, "next": next_record})
    hub._current[1] = "current"
    hub._pending[1] = ["next"]
    task = _Task()
    hub._tasks["current"] = task
    hub._runners["next"] = lambda: None
    started = []
    monkeypatch.setattr(
        hub, "_start", lambda operation_id, _factory=None: started.append(operation_id)
    )
    aborted = await hub.abort(1, message="cancelled")
    assert aborted["status"] == "failed" and task.cancelled
    assert hub._current[1] == "next" and started == ["next"]
    assert await hub.finish("current", success=True, message="late") == aborted
    assert await hub.abort(99, message="none") is None

    hub._current[2] = "finished"
    hub._pending[2] = ["lost"]
    hub._records["lost"] = _record("lost", server_id=2)
    await hub._promote_next(2, "finished")
    assert hub._records["lost"]["status"] == "failed"
    await hub.forward_progress(1, "stdout", "line", "now")
    await hub.forward_progress(99, "stdout", "ignored", "now")


@pytest.mark.asyncio
async def test_emit_subscribe_replay_and_wait_paths(hub, monkeypatch):
    record = await hub.create(server_id=1, action="deploy", actor_user_id=1)
    operation_id = record["operation_id"]
    queue = await hub.subscribe_queue(operation_id)
    await hub.emit(operation_id, "progress", kind="output", message="token=secret")
    event = await queue.get()
    assert "secret" not in event["message"] or event["message"]
    await hub.unsubscribe_queue(operation_id, queue)
    await hub.unsubscribe_queue(operation_id, asyncio.Queue())
    hub._events[operation_id].append({"sequence": "not-int", "message": "bad"})
    replay = await hub.replay(operation_id)
    assert replay and all(event.get("sequence") != "not-int" for event in replay)

    active = await hub.create(server_id=2, action="wait", actor_user_id=1)
    with pytest.raises(TimeoutError):
        await hub.wait_until_terminal(active["operation_id"], timeout=0.001)
    with pytest.raises(LookupError):
        await hub.wait_until_terminal("missing")

    redis = module.redis_manager
    redis.client.lrange = AsyncMock(
        return_value=[json.dumps({"sequence": "5", "message": "loaded"}), "bad"]
    )
    hub._events["loaded"] = []
    loaded = await hub.replay("loaded", after_sequence=1)
    assert loaded[0]["message"] == "loaded"
    redis.client.lrange = AsyncMock(side_effect=RuntimeError("offline"))
    assert await hub.replay("offline") == []


@pytest.mark.asyncio
async def test_pending_failed_current_parsing_and_patch_update(hub, monkeypatch):
    redis = module.redis_manager
    redis.get = AsyncMock(side_effect=[["a", "", 2], '["b", null]', "bad", None])
    assert await hub._pending_ids_unlocked(1) == ["a", "2"]
    assert await hub._pending_ids_unlocked(2) == ["b"]
    assert await hub._pending_ids_unlocked(3) == []
    assert await hub._pending_ids_unlocked(4) == []
    redis.get = AsyncMock(side_effect=['["f"]', "bad"])
    assert await hub._failed_ids_unlocked(1) == ["f"]
    assert await hub._failed_ids_unlocked(2) == []

    hub._records["op"] = _record("op")
    assert (await hub.patch("op", target_path="/tmp", ignored="x"))["target_path"] == "/tmp"
    assert await hub.patch("op", ignored="x")
    redis.get = AsyncMock(return_value=None)
    assert await hub.patch("missing", target_path="/x") is None
    hub._records.clear()
    redis.get = AsyncMock(return_value=_record("op"))
    assert (await hub.patch("op", archive_path="/a"))["archive_path"] == "/a"

    monkeypatch.setattr(hub, "_persist_record", AsyncMock())
    await hub._update("missing", status="running")
    await hub._read_current(99)
    redis.get = AsyncMock(return_value="remote")
    assert await hub._read_current(99) is None
    hub._current[4] = "remote"
    redis.get = AsyncMock(return_value=_record("remote", server_id=4))
    assert (await hub._read_current(4))["operation_id"] == "remote"


@pytest.mark.asyncio
async def test_persistence_and_load_error_paths(monkeypatch):
    hub = module.ServerOperationHub()
    redis = _Redis()
    monkeypatch.setattr(module, "redis_manager", redis)
    redis.set = AsyncMock(side_effect=RuntimeError("set"))
    await hub._persist_record(_record("op"))
    await hub._persist_pending(1)
    await hub._persist_failed(1)
    await hub._forget_operation("op")
    redis.client.expire = AsyncMock(side_effect=RuntimeError("expire"))
    await hub._expire_events("op", 10)
    redis.client.pipeline = lambda **_kwargs: _Pipeline(error=RuntimeError("pipe"))
    await hub._persist_event("op", {"sequence": "1"})
    redis.client.lrange = AsyncMock(return_value=[json.dumps({"x": 1}), b"bad"])
    assert await hub._load_events("op") == [{"x": 1}]
    redis.client.lrange = AsyncMock(side_effect=RuntimeError("lrange"))
    assert await hub._load_events("op") == []


@pytest.mark.asyncio
async def test_list_for_server_and_lost_pending_worker(hub):
    hub._records.update(
        {"a": _record("a", server_id=1), "b": _record("b", server_id=1, status="completed")}
    )
    hub._current[1] = "a"
    hub._pending[1] = ["a", "b"]
    listed = await hub.list_for_server(1)
    assert [item["operation_id"] for item in listed] == ["a", "b"]
    assert await hub.list_for_server(99) == []
    hub._current.clear()
    hub._current[2] = "finished"
    hub._pending[2] = ["lost"]
    hub._records["lost"] = _record("lost", server_id=2)
    await hub._promote_next(2, "finished")
    assert hub._records["lost"]["status"] == "failed"
