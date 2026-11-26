"""Tests for bulk marketplace release sync, invalid-plugin scanning, and v1 routes."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from api.contracts.v1.plugins import MarketPluginReleaseSyncRequest
from api.routes.v1 import operation_inbox as inbox_routes
from api.routes.v1 import plugin_release_sync_jobs as routes
from modules.models import MarketPlugin, PluginFramework, PluginReleaseSyncJob
from services.operations.inbox_types import InboxPayload
from services.plugins import release_sync_runner as runner
from services.plugins import release_sync_store as store
from services.plugins import release_sync_worker as worker
from services.plugins.common import PluginPlanError
from services.plugins.github_ai_client import (
    GitHubAuthenticationError,
    GitHubImportError,
    GitHubRateLimitError,
)
from services.plugins.github_assets import GitHubPlanError
from services.plugins.release_selection import MarketReleaseSync, SyncedAsset
from services.plugins.types import ReleaseSyncItem

REPO = "https://github.com/acme/sample-plugin"
ASSET_NAME = "sample-plugin-v1.2.0-linux.tar.gz"
ASSET_URL = f"{REPO}/releases/download/v1.2.0/{ASSET_NAME}"
SHA256 = "b" * 64


class FakeResult:
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


class FakeSession:
    def __init__(self, statements=(), scalar_value=0, objects=None):
        self.statements = list(statements)
        self.scalar_value = scalar_value
        self.objects = objects or {}
        self.added = []
        self.deleted = []
        self.expunged = []
        self.commits = 0
        self.refreshes = 0

    async def execute(self, _statement):
        if self.statements:
            value = self.statements.pop(0)
            return value if isinstance(value, FakeResult) else FakeResult(value)
        return FakeResult()

    async def scalar(self, _statement):
        return self.scalar_value

    async def get(self, model, key, **_kwargs):
        return self.objects.get((model, key))

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)

    def expunge(self, value):
        self.expunged.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        self.refreshes += 1


def _session_factory(session: FakeSession):
    @asynccontextmanager
    async def maker():
        yield session

    return maker


def _job(**overrides) -> PluginReleaseSyncJob:
    current = datetime.now(timezone.utc)
    values = {
        "id": "22222222-2222-2222-2222-222222222222",
        "actor_user_id": 1,
        "request_key": "release_sync:1:req",
        "scope_key": "scope:sync",
        "mode": "sync",
        "status": "queued",
        "framework": "counterstrikesharp",
        "command": "Sync plugin releases",
        "target_ids": [1, 2],
        "total": 2,
        "created_at": current,
        "heartbeat_at": current,
        "phase": "queued",
        "message": "Queued",
    }
    values.update(overrides)
    return PluginReleaseSyncJob(**values)


def _release(*asset_names: str, tag: str = "v1.2.0", draft: bool = False, prerelease: bool = False):
    return {
        "id": 88,
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": [
            {
                "id": index,
                "name": name,
                "browser_download_url": f"{REPO}/releases/download/{tag}/{name}",
                "digest": f"sha256:{SHA256}",
            }
            for index, name in enumerate(asset_names, 10)
        ],
    }


def _snapshot(tag: str = "v1.2.0", name: str = ASSET_NAME) -> MarketReleaseSync:
    return MarketReleaseSync(
        revision=uuid4().hex,
        repository=REPO.casefold(),
        release_id="88",
        tag=tag,
        assets=[
            SyncedAsset(
                id="10",
                name=name,
                url=f"{REPO}/releases/download/{tag}/{name}",
                sha256=SHA256,
                entrypoints=["addons/counterstrikesharp/plugins/Sample/Sample.dll"],
            )
        ],
    )


@pytest.fixture
def store_env(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(store, "async_session_maker", _session_factory(session))
    monkeypatch.setattr(store, "authorize", AsyncMock())
    monkeypatch.setattr(store, "_target_ids", AsyncMock(return_value=[10, 20]))
    monkeypatch.setattr(store.redis_manager, "acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(store.redis_manager, "release_lock", AsyncMock(return_value=True))
    return session


@pytest.mark.asyncio
async def test_store_enqueue_sync_and_scan_with_dedup_and_limits(store_env, monkeypatch):
    store_env.statements = [FakeResult(), FakeResult()]
    created = await store.enqueue(
        1,
        uuid4(),
        mode="scan",
        framework=PluginFramework.COUNTERSTRIKESHARP,
    )
    assert created.mode == "scan"
    assert created.total == 2
    assert "Scan invalid plugins" in created.command
    assert store_env.commits == 1

    existing = _job(mode="sync")
    store_env.statements = [FakeResult([existing])]
    assert (await store.enqueue(1, uuid4(), mode="sync")).operation_id == existing.id

    store_env.statements = [FakeResult(), FakeResult([existing])]
    assert (await store.enqueue(1, uuid4(), mode="sync")).operation_id == existing.id

    store_env.statements = [FakeResult(), FakeResult()]
    store_env.scalar_value = store.MAX_PENDING_JOBS
    with pytest.raises(ValueError, match="queue is full"):
        await store.enqueue(1, uuid4())

    monkeypatch.setattr(store.redis_manager, "acquire_lock", AsyncMock(return_value=False))
    with pytest.raises(RuntimeError, match="unavailable"):
        await store.enqueue(1, uuid4())


@pytest.mark.asyncio
async def test_store_record_item_updates_in_sync_mode_and_leaves_scan_read_only(store_env):
    plugin = MarketPlugin(id=10, title="Sample", github_url=REPO, version="v1.0.0")
    sync_job = _job(mode="sync", status="running", target_ids=[10], total=1)
    store_env.statements = [FakeResult([sync_job])]
    store_env.objects[(MarketPlugin, 10)] = plugin
    snap = _snapshot().model_dump(mode="json")

    await store.record_item(
        sync_job.id,
        ReleaseSyncItem(
            plugin_id=10,
            title="Sample",
            github_url=REPO,
            action="updated",
            tag="v1.2.0",
            rule="sample-plugin-*-linux.tar.gz",
            selected_assets=[ASSET_NAME],
        ),
        release_sync=snap,
    )
    assert plugin.release_asset_glob == "sample-plugin-*-linux.tar.gz"
    assert plugin.release_sync == snap
    assert plugin.version == "v1.2.0"
    assert sync_job.cursor == 1 and sync_job.updated == 1

    untouched = MarketPlugin(id=10, title="Sample", github_url=REPO, version="v1.0.0")
    scan_job = _job(mode="scan", status="running", target_ids=[10], total=1)
    store_env.statements = [FakeResult([scan_job])]
    store_env.objects[(MarketPlugin, 10)] = untouched
    await store.record_item(
        scan_job.id,
        ReleaseSyncItem(
            plugin_id=10,
            title="Sample",
            github_url=REPO,
            action="failed",
            issue_code="release_missing",
            cleanable=True,
            message="No release",
        ),
        release_sync=snap,
    )
    assert untouched.release_sync is None
    assert scan_job.failed == 1 and scan_job.cleanable == 1


def test_trim_items_preserves_cleanable_and_flagged_entries(monkeypatch):
    monkeypatch.setattr(store, "MAX_STORED_ITEMS", 4)
    rows = [
        {"plugin_id": 1, "action": "failed", "cleanable": True, "issue_code": "release_missing"},
        {"plugin_id": 2, "action": "unchanged", "cleanable": False, "issue_code": None},
        {"plugin_id": 3, "action": "unchanged", "cleanable": False, "issue_code": None},
        {
            "plugin_id": 4,
            "action": "skipped",
            "cleanable": False,
            "issue_code": "manual_selection_required",
        },
        {"plugin_id": 5, "action": "unchanged", "cleanable": False, "issue_code": None},
        {"plugin_id": 6, "action": "unchanged", "cleanable": False, "issue_code": None},
    ]
    trimmed = store._trim_items(rows)
    assert len(trimmed) == 4
    kept_ids = [row["plugin_id"] for row in trimmed]
    assert 1 in kept_ids and 4 in kept_ids


@pytest.mark.asyncio
async def test_store_lifecycle_claim_reconcile_cancel_delete_and_clear(store_env):
    running_job = _job(status="running", cursor=1)
    cancelled_running = _job(status="running", cancel_requested=True)
    store_env.statements = [FakeResult([running_job, cancelled_running])]
    assert await store.reconcile_orphans() == 2
    assert running_job.status == "queued" and running_job.cursor == 1
    assert cancelled_running.status == "cancelled"

    queued = _job(status="queued")
    store_env.statements = [FakeResult([queued])]
    claimed = await store.claim_next()
    assert claimed is not None and claimed.status == "running"

    pre_cancelled = _job(status="queued", cancel_requested=True)
    store_env.statements = [FakeResult([pre_cancelled])]
    claimed_cancelled = await store.claim_next()
    assert claimed_cancelled is not None and claimed_cancelled.status == "cancelled"

    cancel_target = _job(status="queued")
    store_env.statements = [FakeResult([cancel_target])]
    after_cancel = await store.cancel_job(cancel_target.id, 1)
    assert after_cancel.status == "cancelled"

    active_delete = _job(status="running")
    store_env.objects[(PluginReleaseSyncJob, active_delete.id)] = active_delete
    with pytest.raises(ValueError, match="Cancel"):
        await store.delete_job(active_delete.id, 1)

    done_delete = _job(status="completed")
    store_env.objects[(PluginReleaseSyncJob, done_delete.id)] = done_delete
    await store.delete_job(done_delete.id, 1)
    assert done_delete in store_env.deleted

    store_env.scalar_value = 3
    assert await store.clear_failed_jobs(1) == 3


class ScriptedGitHubClient:
    def __init__(self, responses: dict[str, object]):
        self.responses = dict(responses)
        self.calls: list[str] = []
        self.closed = False

    async def request(self, path: str, **_kwargs):
        self.calls.append(path)
        outcome = self.responses.get(path)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def optional(self, path: str, **_kwargs):
        try:
            return await self.request(path)
        except GitHubImportError as exc:
            if exc.status == 404:
                return None
            raise

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_evaluate_listing_covers_framework_missing_ambiguous_cached_and_broken(monkeypatch):
    client = ScriptedGitHubClient({})
    framework_listing = MarketPlugin(
        id=1,
        title="CounterStrikeSharp",
        github_url="https://github.com/roflmuffin/CounterStrikeSharp",
    )
    skipped = await runner.evaluate_listing(client, framework_listing, mode="scan")
    assert skipped.item.action == "skipped" and skipped.item.cleanable is False
    assert client.calls == []

    bad_url = MarketPlugin(id=2, title="Bad", github_url="https://example.com/not-github")
    invalid_url = await runner.evaluate_listing(client, bad_url, mode="scan")
    assert invalid_url.item.issue_code == "invalid_url" and invalid_url.item.cleanable is True

    bad_ai = MarketPlugin(
        id=3, title="BadAI", github_url=REPO, ai_metadata={"invalid_field": object()}
    )
    monkeypatch.setattr(
        runner, "validate_installable", lambda _p: (_ for _ in ()).throw(PluginPlanError("bad ai"))
    )
    invalid_ai = await runner.evaluate_listing(client, bad_ai, mode="scan")
    assert invalid_ai.item.issue_code == "archive_invalid" and invalid_ai.item.cleanable is True
    monkeypatch.undo()

    repo_404 = ScriptedGitHubClient(
        {
            "/repos/acme/sample-plugin/releases/latest": GitHubImportError("404", status=404),
            "/repos/acme/sample-plugin": GitHubImportError("404", status=404),
        }
    )
    missing_repo = await runner.evaluate_listing(
        repo_404, MarketPlugin(id=4, title="Gone", github_url=REPO), mode="scan"
    )
    assert (
        missing_repo.item.issue_code == "repository_missing" and missing_repo.item.cleanable is True
    )

    no_release = ScriptedGitHubClient(
        {
            "/repos/acme/sample-plugin/releases/latest": GitHubImportError("404", status=404),
            "/repos/acme/sample-plugin": {"id": 1, "private": False},
        }
    )
    missing_rel = await runner.evaluate_listing(
        no_release, MarketPlugin(id=5, title="NoRel", github_url=REPO), mode="scan"
    )
    assert missing_rel.item.issue_code == "release_missing" and missing_rel.item.cleanable is True

    upstream_502 = ScriptedGitHubClient(
        {"/repos/acme/sample-plugin/releases/latest": GitHubImportError("HTTP 502", status=502)}
    )
    transient = await runner.evaluate_listing(
        upstream_502, MarketPlugin(id=6, title="Transient", github_url=REPO), mode="scan"
    )
    assert transient.item.issue_code == "upstream_error" and transient.item.cleanable is False

    win_only = ScriptedGitHubClient(
        {"/repos/acme/sample-plugin/releases/latest": _release("plugin-windows.zip")}
    )
    no_linux = await runner.evaluate_listing(
        win_only, MarketPlugin(id=7, title="WinOnly", github_url=REPO), mode="scan"
    )
    assert no_linux.item.issue_code == "no_linux_asset" and no_linux.item.cleanable is True

    multi_pkg = ScriptedGitHubClient(
        {"/repos/acme/sample-plugin/releases/latest": _release("mysql.zip", "sqlite.zip")}
    )
    ambiguous = await runner.evaluate_listing(
        multi_pkg, MarketPlugin(id=8, title="Multi", github_url=REPO), mode="scan"
    )
    assert (
        ambiguous.item.action == "skipped"
        and ambiguous.item.issue_code == "manual_selection_required"
        and ambiguous.item.cleanable is False
    )

    existing_snap = _snapshot()
    already = MarketPlugin(
        id=9,
        title="Synced",
        github_url=REPO,
        release_asset_glob="sample-plugin-*-linux.tar.gz",
        release_sync=existing_snap.model_dump(mode="json"),
    )
    unchanged_client = ScriptedGitHubClient(
        {"/repos/acme/sample-plugin/releases/latest": _release(ASSET_NAME)}
    )
    prepare_mock = AsyncMock()
    monkeypatch.setattr(runner, "_prepare_snapshot", prepare_mock)
    unchanged = await runner.evaluate_listing(unchanged_client, already, mode="sync")
    assert unchanged.item.action == "unchanged"
    prepare_mock.assert_not_awaited()

    fresh = MarketPlugin(id=10, title="Fresh", github_url=REPO)
    prepare_mock.return_value = existing_snap
    synced = await runner.evaluate_listing(unchanged_client, fresh, mode="sync")
    assert synced.item.action == "updated" and synced.release_sync is not None

    scanned = await runner.evaluate_listing(unchanged_client, fresh, mode="scan")
    assert scanned.item.action == "unchanged" and scanned.release_sync is None

    prepare_mock.side_effect = PluginPlanError("archive layout is not a recognized CS2 plugin")
    broken_archive = await runner.evaluate_listing(unchanged_client, fresh, mode="scan")
    assert (
        broken_archive.item.issue_code == "archive_invalid"
        and broken_archive.item.cleanable is True
    )

    prepare_mock.side_effect = httpx.ConnectError("network down")
    net_err = await runner.evaluate_listing(unchanged_client, fresh, mode="scan")
    assert net_err.item.issue_code == "upstream_error" and net_err.item.cleanable is False


@pytest.mark.asyncio
async def test_runner_handles_rate_limit_auth_failure_and_completion(monkeypatch):
    monkeypatch.setattr(runner, "record_audit_event", AsyncMock())
    monkeypatch.setattr(store, "credentials", AsyncMock(return_value="tok"))
    monkeypatch.setattr(store, "is_cancel_requested", AsyncMock(return_value=False))
    monkeypatch.setattr(
        store,
        "load_target",
        AsyncMock(return_value=MarketPlugin(id=1, title="One", github_url=REPO)),
    )
    monkeypatch.setattr(store, "set_current", AsyncMock())
    waiting = AsyncMock()
    record = AsyncMock()
    finish = AsyncMock(return_value=store.snapshot(_job(status="completed", cursor=1, total=1)))
    monkeypatch.setattr(store, "mark_waiting", waiting)
    monkeypatch.setattr(store, "record_item", record)
    monkeypatch.setattr(store, "finish_job", finish)

    class RateLimitedClient(ScriptedGitHubClient):
        def __init__(self, _token, **_kwargs):
            super().__init__(
                {
                    "/repos/acme/sample-plugin/releases/latest": GitHubRateLimitError(
                        "rate limited", reset_at=456
                    )
                }
            )

    monkeypatch.setattr(runner, "GitHubAIClient", RateLimitedClient)
    await runner.run_job(store.snapshot(_job(target_ids=[1], total=1)))
    waiting.assert_awaited_once()
    assert waiting.await_args.kwargs["retry_at"] == 456
    record.assert_not_awaited()

    class AuthErrorClient(ScriptedGitHubClient):
        def __init__(self, _token, **_kwargs):
            super().__init__(
                {
                    "/repos/acme/sample-plugin/releases/latest": GitHubAuthenticationError(
                        "bad token", status=401
                    )
                }
            )

    monkeypatch.setattr(runner, "GitHubAIClient", AuthErrorClient)
    await runner.run_job(store.snapshot(_job(target_ids=[1], total=1)))
    assert finish.await_args.kwargs["reason"] == "configuration"


@pytest.mark.asyncio
async def test_worker_wait_until_and_routes_and_inbox(monkeypatch):
    monkeypatch.setattr(worker.time, "time", lambda: 200)
    monkeypatch.setattr(worker.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        worker.store, "get_job", AsyncMock(return_value=SimpleNamespace(status="cancelled"))
    )
    assert await worker.ReleaseSyncWorker._wait_until("job-1", 210) is False

    snap = store.snapshot(_job(mode="scan", cleanable=2, failed=2))
    monkeypatch.setattr(routes.release_sync_store, "enqueue", AsyncMock(return_value=snap))
    monkeypatch.setattr(routes.release_sync_store, "list_jobs", AsyncMock(return_value=[snap]))
    monkeypatch.setattr(routes.release_sync_store, "get_job", AsyncMock(return_value=snap))
    monkeypatch.setattr(routes.release_sync_store, "cancel_job", AsyncMock(return_value=snap))
    monkeypatch.setattr(routes.release_sync_store, "delete_job", AsyncMock())
    monkeypatch.setattr(routes, "record_audit_event", AsyncMock())

    admin = SimpleNamespace(id=1, is_admin=True)
    req_body = MarketPluginReleaseSyncRequest(
        request_id=uuid4(), mode="scan", framework="counterstrikesharp"
    )
    enqueued = await routes.enqueue_market_release_sync(req_body, admin, None)  # type: ignore[arg-type]
    assert enqueued.mode == "scan" and enqueued.cleanable == 2

    with pytest.raises(HTTPException) as bad_fw:
        await routes._parse_framework_filter("unknown")
    assert bad_fw.value.status_code == 400

    listed = await routes.list_market_release_syncs(admin, mode="scan")
    assert len(listed) == 1 and listed[0].operation_id == snap.operation_id

    op_uuid = UUID(snap.operation_id)
    assert (await routes.get_market_release_sync(op_uuid, admin)).cleanable == 2
    assert (
        await routes.cancel_market_release_sync(op_uuid, admin)
    ).operation_id == snap.operation_id
    assert (await routes.delete_market_release_sync(op_uuid, admin)).success is True

    inbox_view = inbox_routes.to_inbox_view(
        InboxPayload(items=[], completed_items=[], failed_items=[], release_jobs=[snap])
    )
    assert len(inbox_view.market_release_items) == 1
    assert inbox_view.market_release_items[0].cleanable == 2

    monkeypatch.setattr(inbox_routes, "load_accessible_servers", AsyncMock(return_value=[]))
    monkeypatch.setattr(inbox_routes, "close_request_session", AsyncMock())
    monkeypatch.setattr(
        inbox_routes.server_operation_hub, "clear_failed", AsyncMock(return_value=1)
    )
    monkeypatch.setattr(inbox_routes, "clear_failed_jobs", AsyncMock(return_value=1))
    monkeypatch.setattr(inbox_routes, "clear_failed_description_jobs", AsyncMock(return_value=1))
    monkeypatch.setattr(inbox_routes, "clear_failed_release_jobs", AsyncMock(return_value=2))
    cleared = await inbox_routes.clear_failed_operations(None, admin)  # type: ignore[arg-type]
    assert "5" in cleared.message


@pytest.mark.asyncio
async def test_store_remaining_helpers_and_observability(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(store, "async_session_maker", _session_factory(session))

    with pytest.raises(PermissionError):
        await store.authorize(99)
    with pytest.raises(PermissionError):
        await store.credentials(99)

    admin_user = SimpleNamespace(id=1, is_active=True, is_admin=True)
    session.objects[(store.User, 1)] = admin_user
    await store.authorize(1)
    monkeypatch.setattr(store, "get_effective_github_token", AsyncMock(return_value="ghp_test"))
    assert await store.credentials(1) == "ghp_test"

    session.statements = [FakeResult([1, 3])]
    assert await store._target_ids([1, 3, 1], PluginFramework.SWIFTLY) == [1, 3]

    job = _job(
        status="running",
        items=[
            {
                "plugin_id": 1,
                "title": "One",
                "github_url": REPO,
                "action": "updated",
                "tag": "v1.0.0",
                "rule": "*.zip",
                "selected_assets": ["one.zip"],
                "issue_code": None,
                "cleanable": False,
                "message": "ok",
            }
        ],
    )
    session.objects[(PluginReleaseSyncJob, job.id)] = job
    loaded = await store.get_job(job.id)
    assert loaded is not None and loaded.items[0].selected_assets == ["one.zip"]
    assert await store.get_job("missing") is None

    session.statements = [FakeResult([job])]
    listed = await store.list_jobs(mode="sync")
    assert len(listed) == 1

    plugin = MarketPlugin(id=5, title="Target", github_url=REPO)
    session.objects[(MarketPlugin, 5)] = plugin
    assert await store.load_target(5) is plugin
    assert plugin in session.expunged
    assert await store.load_target(999) is None

    await store.set_current(job.id, plugin)
    assert job.current_plugin_id == 5 and "Syncing release" in job.message
    job.mode = "scan"
    await store.set_current(job.id, plugin)
    assert "Checking" in job.message

    await store.mark_waiting(job.id, retry_at=999, message="wait")
    assert job.status == "waiting" and job.retry_at == 999
    await store.requeue_job(job.id, "resume later")
    assert job.status == "queued"
    assert await store.is_cancel_requested(job.id) is False

    completed = await store.finish_job(
        job.id, status="completed", phase="completed", message="done"
    )
    assert completed is not None and completed.status == "completed"
    # Second finish on terminal job is a no-op returning existing snapshot
    assert (
        await store.finish_job(job.id, status="failed", phase="failed", message="ignored")
    ) is not None

    fail_job = _job(id="33333333-3333-3333-3333-333333333333", status="running")
    session.objects[(PluginReleaseSyncJob, fail_job.id)] = fail_job
    finished_fail = await store.finish_job(
        fail_job.id, status="failed", phase="failed", message="boom", reason="err"
    )
    assert finished_fail is not None and finished_fail.stop_reason == "err"

    cancel_job_obj = _job(id="44444444-4444-4444-4444-444444444444", status="running")
    session.objects[(PluginReleaseSyncJob, cancel_job_obj.id)] = cancel_job_obj
    finished_cancel = await store.finish_job(
        cancel_job_obj.id, status="cancelled", phase="cancelled", message="stop"
    )
    assert finished_cancel is not None and finished_cancel.status == "cancelled"

    with pytest.raises(LookupError):
        await store.cancel_job("missing", 1)
    with pytest.raises(LookupError):
        await store.delete_job("missing", 1)


@pytest.mark.asyncio
async def test_runner_full_branches_cancel_missing_draft_and_errors(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(runner, "record_audit_event", audit)

    # Draft, bad release id, 410 repo, already-synced changed asset, transient HTTP 503
    gone_client = ScriptedGitHubClient(
        {"/repos/acme/sample-plugin/releases/latest": GitHubImportError("410", status=410)}
    )
    gone_eval = await runner.evaluate_listing(
        gone_client, MarketPlugin(id=1, title="Gone", github_url=REPO), mode="scan"
    )
    assert gone_eval.item.issue_code == "repository_missing"

    draft_client = ScriptedGitHubClient(
        {"/repos/acme/sample-plugin/releases/latest": _release(ASSET_NAME, draft=True)}
    )
    draft_eval = await runner.evaluate_listing(
        draft_client, MarketPlugin(id=2, title="Draft", github_url=REPO), mode="scan"
    )
    assert draft_eval.item.issue_code == "release_missing"

    bad_id_client = ScriptedGitHubClient(
        {"/repos/acme/sample-plugin/releases/latest": {"id": "", "tag_name": ""}}
    )
    bad_id_eval = await runner.evaluate_listing(
        bad_id_client, MarketPlugin(id=3, title="BadId", github_url=REPO), mode="scan"
    )
    assert bad_id_eval.item.issue_code == "release_missing"

    stale_sync = MarketPlugin(
        id=4,
        title="Stale",
        github_url=REPO,
        release_asset_glob="sample-plugin-*-linux.tar.gz",
        release_sync=_snapshot(tag="v1.0.0").model_dump(mode="json"),
    )
    monkeypatch.setattr(
        runner,
        "_prepare_snapshot",
        AsyncMock(side_effect=GitHubPlanError("Release asset download failed with HTTP 503")),
    )
    ok_client = ScriptedGitHubClient(
        {"/repos/acme/sample-plugin/releases/latest": _release(ASSET_NAME)}
    )
    http503_eval = await runner.evaluate_listing(ok_client, stale_sync, mode="sync")
    assert http503_eval.item.issue_code == "upstream_error" and http503_eval.item.cleanable is False

    # Full run_job completion with missing listing + normal listing
    monkeypatch.setattr(runner, "_prepare_snapshot", AsyncMock(return_value=_snapshot()))
    monkeypatch.setattr(store, "credentials", AsyncMock(return_value=None))
    monkeypatch.setattr(store, "is_cancel_requested", AsyncMock(return_value=False))
    monkeypatch.setattr(
        store,
        "load_target",
        AsyncMock(side_effect=[None, MarketPlugin(id=2, title="Two", github_url=REPO)]),
    )
    monkeypatch.setattr(store, "set_current", AsyncMock())
    record = AsyncMock()
    monkeypatch.setattr(store, "record_item", record)
    finish = AsyncMock(
        return_value=store.snapshot(_job(mode="scan", status="completed", cursor=2, total=2))
    )
    monkeypatch.setattr(store, "finish_job", finish)

    class HappyClient(ScriptedGitHubClient):
        def __init__(self, _token, **_kwargs):
            super().__init__({"/repos/acme/sample-plugin/releases/latest": _release(ASSET_NAME)})

    monkeypatch.setattr(runner, "GitHubAIClient", HappyClient)
    await runner.run_job(store.snapshot(_job(mode="scan", target_ids=[1, 2], total=2)))
    assert record.await_count == 2
    assert finish.await_args.kwargs["status"] == "completed"
    assert audit.await_args.kwargs["action"] == "plugin.catalog.scan_invalid"

    # Cancel requested mid-run
    monkeypatch.setattr(store, "is_cancel_requested", AsyncMock(return_value=True))
    finish.return_value = store.snapshot(_job(status="cancelled"))
    await runner.run_job(store.snapshot(_job(target_ids=[1], total=1)))
    assert finish.await_args.kwargs["status"] == "cancelled"

    # Permission error before client creation
    monkeypatch.setattr(store, "credentials", AsyncMock(side_effect=PermissionError("revoked")))
    finish.return_value = store.snapshot(_job(status="failed"))
    await runner.run_job(store.snapshot(_job(target_ids=[1], total=1)))
    assert finish.await_args.kwargs["reason"] == "configuration"

    # CancelledError interruption requeues when cancel_requested is False
    monkeypatch.setattr(store, "credentials", AsyncMock(return_value=None))
    monkeypatch.setattr(store, "is_cancel_requested", AsyncMock(return_value=False))
    requeue = AsyncMock()
    monkeypatch.setattr(store, "requeue_job", requeue)
    monkeypatch.setattr(store, "load_target", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await runner.run_job(store.snapshot(_job(target_ids=[1], total=1)))
    requeue.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_consume_and_route_errors_and_sse(monkeypatch):
    w = worker.ReleaseSyncWorker()
    monkeypatch.setattr(worker, " LOCK", "lock", raising=False)
    monkeypatch.setattr(worker.store, "reconcile_orphans", AsyncMock(return_value=0))
    refresh = AsyncMock(side_effect=[True, True, False])
    monkeypatch.setattr(worker.redis_manager, "refresh_lock", refresh)
    job_snap = store.snapshot(_job(status="running", target_ids=[], total=0))
    monkeypatch.setattr(worker.store, "claim_next", AsyncMock(side_effect=[None, job_snap]))
    monkeypatch.setattr(worker.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(worker, "run_job", AsyncMock())
    await w.consume("token-1")

    admin = SimpleNamespace(id=1, is_admin=True)
    member = SimpleNamespace(id=2, is_admin=False)
    op_id = uuid4()
    body = MarketPluginReleaseSyncRequest(request_id=uuid4(), mode="sync")

    for err, expected in (
        (PermissionError("denied"), 403),
        (ValueError("full"), 409),
        (RuntimeError("down"), 503),
    ):
        monkeypatch.setattr(routes.release_sync_store, "enqueue", AsyncMock(side_effect=err))
        with pytest.raises(HTTPException) as exc_info:
            await routes.enqueue_market_release_sync(body, admin, None)  # type: ignore[arg-type]
        assert exc_info.value.status_code == expected

    monkeypatch.setattr(routes.release_sync_store, "get_job", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as not_found:
        await routes.get_market_release_sync(op_id, admin)
    assert not_found.value.status_code == 404

    monkeypatch.setattr(
        routes.release_sync_store, "cancel_job", AsyncMock(side_effect=LookupError("missing"))
    )
    with pytest.raises(HTTPException) as cancel_404:
        await routes.cancel_market_release_sync(op_id, admin)
    assert cancel_404.value.status_code == 404

    for err, expected in ((LookupError("missing"), 404), (ValueError("active"), 409)):
        monkeypatch.setattr(routes.release_sync_store, "delete_job", AsyncMock(side_effect=err))
        with pytest.raises(HTTPException) as del_err:
            await routes.delete_market_release_sync(op_id, admin)
        assert del_err.value.status_code == expected

    with pytest.raises(HTTPException) as sse_403:
        await routes.stream_market_release_sync(op_id, None, member)  # type: ignore[arg-type]
    assert sse_403.value.status_code == 403

    with pytest.raises(HTTPException) as sse_404:
        await routes.stream_market_release_sync(op_id, None, admin)  # type: ignore[arg-type]
    assert sse_404.value.status_code == 404

    completed_snap = store.snapshot(_job(status="completed", cursor=2, total=2))
    monkeypatch.setattr(
        routes.release_sync_store, "get_job", AsyncMock(return_value=completed_snap)
    )
    fake_req = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    response = await routes.stream_market_release_sync(op_id, fake_req, admin)  # type: ignore[arg-type]
    frames = [chunk async for chunk in response.body_iterator]
    assert any("event: snapshot" in str(frame) for frame in frames)
