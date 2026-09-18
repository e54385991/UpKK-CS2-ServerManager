"""Persistent queue and serial execution semantics for description syncs."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from modules.models import MarketPlugin, PluginDescriptionSyncJob
from services.plugins import description_sync_runner as runner
from services.plugins import description_sync_store as store
from services.plugins import description_sync_worker as worker
from services.plugins.github_ai_client import GitHubAIClient, GitHubRateLimitError


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Session:
    def __init__(self, statements=(), scalar_value=0, objects=None):
        self.statements = list(statements)
        self.scalar_value = scalar_value
        self.objects = objects or {}
        self.added = []
        self.commits = 0
        self.refreshes = 0

    async def execute(self, _statement):
        if self.statements:
            value = self.statements.pop(0)
            return value if isinstance(value, Result) else Result(value)
        return Result()

    async def scalar(self, _statement):
        return self.scalar_value

    async def get(self, model, key):
        return self.objects.get((model, key))

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        self.refreshes += 1


def _session_factory(session: Session):
    @asynccontextmanager
    async def maker():
        yield session

    return maker


def _job(**overrides) -> PluginDescriptionSyncJob:
    current = datetime.now(timezone.utc)
    values = {
        "id": "11111111-1111-1111-1111-111111111111",
        "actor_user_id": 1,
        "request_key": "description_sync:1:request",
        "scope_key": "scope:overwrite",
        "status": "queued",
        "framework": "counterstrikesharp",
        "overwrite": True,
        "command": "Sync descriptions",
        "target_ids": [1, 2],
        "total": 2,
        "created_at": current,
        "heartbeat_at": current,
        "phase": "queued",
        "message": "Queued",
    }
    values.update(overrides)
    return PluginDescriptionSyncJob(**values)


@pytest.fixture
def store_env(monkeypatch):
    session = Session()
    monkeypatch.setattr(store, "async_session_maker", _session_factory(session))
    monkeypatch.setattr(store, "authorize", AsyncMock())
    monkeypatch.setattr(store, "_target_ids", AsyncMock(return_value=[1, 2]))
    monkeypatch.setattr(store.redis_manager, "acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(store.redis_manager, "release_lock", AsyncMock(return_value=True))
    return session


@pytest.mark.asyncio
async def test_enqueue_persists_target_snapshot_and_idempotency_key(store_env):
    store_env.statements = [Result(), Result()]
    job = await store.enqueue(
        1,
        uuid4(),
        framework=SimpleNamespace(value="counterstrikesharp"),
        overwrite=True,
    )

    assert job.total == 2
    assert job.target_ids == [1, 2]
    assert store_env.added[0].target_ids == [1, 2]
    assert store_env.added[0].scope_key
    assert store_env.commits == 1


@pytest.mark.asyncio
async def test_enqueue_reuses_request_and_active_scope(store_env):
    existing = _job()
    store_env.statements = [Result([existing])]
    result = await store.enqueue(1, uuid4())
    assert result.operation_id == existing.id

    store_env.statements = [Result(), Result([existing])]
    result = await store.enqueue(1, uuid4())
    assert result.operation_id == existing.id


@pytest.mark.asyncio
async def test_enqueue_rejects_a_full_pending_queue(store_env):
    store_env.statements = [Result(), Result()]
    store_env.scalar_value = store.MAX_PENDING_JOBS
    with pytest.raises(ValueError, match="queue is full"):
        await store.enqueue(1, uuid4())


@pytest.mark.asyncio
async def test_record_item_updates_description_and_cursor_atomically(store_env):
    job = _job(target_ids=[7], total=1)
    plugin = MarketPlugin(
        id=7,
        title="Old",
        github_url="https://github.com/acme/plugin",
        description="old",
        description_i18n={"original": "old"},
    )
    store_env.statements = [Result([job])]
    store_env.objects[(MarketPlugin, 7)] = plugin

    await store.record_item(
        job.id,
        plugin_id=7,
        title="Old",
        github_url=plugin.github_url,
        action="updated",
        description="# New",
    )

    assert plugin.description == "# New"
    assert plugin.description_i18n is None
    assert job.cursor == 1 and job.updated == 1
    assert job.items[0]["action"] == "updated"
    assert store_env.commits == 1


@pytest.mark.asyncio
async def test_reconcile_requeues_running_jobs_without_losing_cursor(store_env):
    job = _job(status="running", cursor=1)
    store_env.statements = [Result([job])]
    assert await store.reconcile_orphans() == 1
    assert job.status == "queued" and job.cursor == 1


@pytest.mark.asyncio
async def test_worker_wait_exits_when_a_waiting_job_is_cancelled(monkeypatch):
    monkeypatch.setattr(worker.time, "time", lambda: 100)
    monkeypatch.setattr(worker.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        worker.store,
        "get_job",
        AsyncMock(return_value=SimpleNamespace(status="cancelled")),
    )

    assert await worker.DescriptionSyncWorker._wait_until("job", 105) is False


class FakeClient:
    instances = []

    def __init__(self, token, **kwargs):
        self.token = token
        self.kwargs = kwargs
        self.requests: list[str] = []
        self.responses: list[object] = []
        self.closed = False
        self.instances.append(self)

    async def request(self, path: str):
        self.requests.append(path)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self):
        self.closed = True


def _readme(text: str) -> dict[str, str]:
    return {"content": base64.b64encode(text.encode()).decode()}


async def _targets(plugin_id: int):
    return store.TargetPlugin(
        plugin_id=plugin_id,
        title=f"Plugin {plugin_id}",
        github_url=f"https://github.com/acme/plugin-{plugin_id}",
        description=None,
    )


@pytest.mark.asyncio
async def test_runner_is_serial_and_commits_each_item(monkeypatch):
    FakeClient.instances.clear()
    monkeypatch.setattr(runner, "GitHubAIClient", FakeClient)
    monkeypatch.setattr(runner, "record_audit_event", AsyncMock())
    monkeypatch.setattr(store, "credentials", AsyncMock(return_value=None))
    monkeypatch.setattr(store, "is_cancel_requested", AsyncMock(return_value=False))
    monkeypatch.setattr(store, "load_target", AsyncMock(side_effect=_targets))
    monkeypatch.setattr(store, "set_current", AsyncMock())
    record = AsyncMock()
    monkeypatch.setattr(store, "record_item", record)
    finish = AsyncMock(return_value=store.snapshot(_job(status="completed", cursor=2)))
    monkeypatch.setattr(store, "finish_job", finish)

    class ConfiguredClient(FakeClient):
        def __init__(self, token, **kwargs):
            super().__init__(token, **kwargs)
            self.responses = [_readme("# One"), _readme("# Two")]

    monkeypatch.setattr(runner, "GitHubAIClient", ConfiguredClient)
    await runner.run_job(store.snapshot(_job()))

    assert record.await_count == 2
    assert [call.kwargs["action"] for call in record.await_args_list] == ["updated", "updated"]
    assert ConfiguredClient.instances[0].kwargs["interval"] == 2.0
    assert ConfiguredClient.instances[0].kwargs["require_token"] is False
    assert ConfiguredClient.instances[0].closed
    assert finish.await_args.kwargs["status"] == "completed"


@pytest.mark.asyncio
async def test_runner_rate_limit_waits_without_advancing_cursor(monkeypatch):
    monkeypatch.setattr(runner, "record_audit_event", AsyncMock())
    monkeypatch.setattr(store, "credentials", AsyncMock(return_value=None))
    monkeypatch.setattr(store, "is_cancel_requested", AsyncMock(return_value=False))
    monkeypatch.setattr(store, "load_target", AsyncMock(side_effect=_targets))
    monkeypatch.setattr(store, "set_current", AsyncMock())
    waiting = AsyncMock()
    monkeypatch.setattr(store, "mark_waiting", waiting)
    record = AsyncMock()
    monkeypatch.setattr(store, "record_item", record)
    finish = AsyncMock()
    monkeypatch.setattr(store, "finish_job", finish)

    class LimitedClient(FakeClient):
        def __init__(self, token, **kwargs):
            super().__init__(token, **kwargs)
            self.responses = [GitHubRateLimitError("limited", reset_at=123)]

    monkeypatch.setattr(runner, "GitHubAIClient", LimitedClient)
    await runner.run_job(store.snapshot(_job(target_ids=[1], total=1)))

    waiting.assert_awaited_once()
    assert waiting.await_args.kwargs["retry_at"] == 123
    record.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_client_can_read_without_a_token():
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = GitHubAIClient(
        None,
        require_token=False,
        interval=0,
        transport=httpx.MockTransport(handle),
    )
    try:
        assert await client.request("/rate_limit") == {"ok": True}
    finally:
        await client.close()
    assert len(requests) == 1
    assert "authorization" not in requests[0].headers
