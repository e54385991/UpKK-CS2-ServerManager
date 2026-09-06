"""Short transaction queue semantics, token replacement and worker recovery."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from modules.models import MarketPlugin, PluginImportJob, SystemSettings, User
from modules.plugin_ai import (
    GitHubVerification,
    ImportOptions,
    InstallationConfig,
    PluginAIInfo,
    RepositoryAnalysis,
)
from services.plugins import ai_import_store as store
from services.plugins import ai_import_worker as worker


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


class Database:
    def __init__(self):
        self.user = SimpleNamespace(id=1, is_admin=True, is_active=True)
        self.job = PluginImportJob(
            actor_user_id=1,
            request_key="test",
            options=ImportOptions().model_dump(),
            command="import",
        )
        self.plugin = MarketPlugin(id=12, title="Original", github_url="https://github.com/a/b")
        self.rows = []
        self.added = []
        self.execute = AsyncMock(side_effect=lambda _: Result(self.rows))
        self.scalar = AsyncMock(return_value=0)
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.flush = AsyncMock()
        self.open_sessions = 0

    async def get(self, model, key):
        if model is User:
            return self.user
        if model is PluginImportJob:
            return self.job
        if model is MarketPlugin:
            return self.plugin
        raise AssertionError(model)

    def add(self, value):
        if isinstance(value, MarketPlugin) and value.id is None:
            value.id = 15
        self.added.append(value)

    @asynccontextmanager
    async def session(self):
        self.open_sessions += 1
        try:
            yield self
        finally:
            self.open_sessions -= 1


@pytest.fixture
def env(monkeypatch):
    db = Database()
    settings = SystemSettings(
        global_github_token="token",
        github_token_fingerprint=store.fingerprint("token"),
        github_token_verification={"valid": True},
    )
    monkeypatch.setattr(store, "async_session_maker", db.session)
    monkeypatch.setattr(SystemSettings, "get_or_create_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        store, "get_effective_provider", AsyncMock(return_value=SimpleNamespace(model="model"))
    )
    monkeypatch.setattr(store.redis_manager, "acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(store.redis_manager, "refresh_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(store.redis_manager, "release_lock", AsyncMock(return_value=True))
    lease = store.worker_lease.set(None)
    yield db, settings
    store.worker_lease.reset(lease)


@pytest.mark.asyncio
async def test_readiness_requires_current_admin_token_and_effective_ai(env):
    db, settings = env
    verified, provider = await store.readiness(1)
    assert verified.valid and provider.model == "model"
    assert (await store.credentials(1))[0] == "token"
    store.get_effective_provider.return_value = None
    with pytest.raises(PermissionError, match="AI provider"):
        await store.credentials(1)
    settings.global_github_token = "replacement"
    with pytest.raises(PermissionError, match="GitHub token"):
        await store.credentials(1)
    db.user.is_admin = False
    with pytest.raises(PermissionError):
        await store.check_administrator(1)
    db.user = None
    with pytest.raises(PermissionError):
        await store.readiness(1)


@pytest.mark.asyncio
async def test_verification_closes_database_during_io_and_rechecks_replacement(env, monkeypatch):
    db, settings = env

    async def verify():
        assert db.open_sessions == 0
        return GitHubVerification(valid=True, account="example")

    monkeypatch.setattr(store.GitHubAIClient, "verify", AsyncMock(side_effect=verify))
    assert (await store.verify_token(1)).valid
    assert settings.github_token_verification["account"] == "example"

    async def replaced():
        settings.global_github_token = "replaced"
        return GitHubVerification(valid=True)

    store.GitHubAIClient.verify.side_effect = replaced
    assert not (await store.verify_token(1)).valid
    from services.plugins.github_ai_client import GitHubImportError

    store.GitHubAIClient.verify.side_effect = GitHubImportError("Network failed")
    assert not (await store.verify_token(1)).valid
    settings.global_github_token = None
    assert not (await store.verify_token(1)).valid


@pytest.mark.asyncio
async def test_enqueue_dedup_pending_cap_and_lease_release(env):
    db, _ = env
    request = uuid4()
    queued = await store.enqueue(1, ImportOptions(), request)
    assert queued.status == "queued" and queued.actor_user_id == 1
    assert db.commit.await_count == 1
    created = db.added[-1]
    assert created.request_key == f"1:{request}"
    db.rows = [created]
    again = await store.enqueue(1, ImportOptions(), request)
    assert again.operation_id == queued.operation_id
    assert db.commit.await_count == 1
    db.rows = []
    db.scalar.return_value = 10
    with pytest.raises(ValueError, match="full"):
        await store.enqueue(1, ImportOptions(), uuid4())
    assert store.redis_manager.release_lock.await_count == 3
    store.redis_manager.acquire_lock.return_value = False
    with pytest.raises(RuntimeError):
        await store.enqueue(1, ImportOptions(), uuid4())


@pytest.mark.asyncio
async def test_cancel_pending_running_and_missing_tasks(env):
    db, _ = env
    db.rows = [db.job]
    cancelled = await store.cancel_job(db.job.id, 1)
    assert cancelled.status == "cancelled" and cancelled.completed_at
    db.job.status = "running"
    requested = await store.cancel_job(db.job.id, 1)
    assert requested.status == "running" and requested.cancel_requested
    db.rows = []
    with pytest.raises(LookupError):
        await store.cancel_job("missing", 1)


@pytest.mark.asyncio
async def test_events_replay_monotonic_and_terminal_writes_noop(env):
    db, _ = env
    db.rows = [db.job]
    for index in range(305):
        store.append_event(db.job, "reading", f"Document {index}")
    assert len(db.job.events) == 300 and db.job.events[-1]["sequence"] == 305
    await store.update_job(
        db.job.id, phase="starting", message="Starting", status="running", model="model"
    )
    assert db.job.started_at and db.job.model == "model"
    from modules.plugin_ai import ImportItem

    await store.update_job(
        db.job.id,
        phase="importing",
        message="Added",
        item=ImportItem(repository="https://github.com/a/b", status="imported", plugin_id=12),
    )
    await store.update_job(
        db.job.id,
        phase="limited",
        message="Limited",
        status="failed",
        reason="github_rate_limit",
        retry_at=123,
    )
    final = await store.get_job(db.job.id)
    assert final.retry_at == 123 and len(final.items) == 1
    await store.update_job(db.job.id, phase="completed", message="Wrong", status="completed")
    assert db.job.status == "failed"
    assert len(await store.list_jobs(active_only=True)) == 1
    assert len(await store.list_jobs()) == 1
    db.job = None
    assert await store.get_job("missing") is None


@pytest.mark.asyncio
async def test_worker_claim_is_fifo_and_restart_reconciles_orphans(env):
    db, _ = env
    db.execute.side_effect = [Result(), Result([db.job])]
    claimed = await store.claim_next()
    assert claimed.status == "running" and claimed.started_at
    statement = str(db.execute.call_args.args[0])
    assert "ORDER BY plugin_import_jobs.created_at" in statement and "FOR UPDATE" in statement
    queued = PluginImportJob(
        actor_user_id=1,
        request_key="queued",
        command="import",
        created_at=store.now() - timedelta(hours=1),
    )
    fresh = PluginImportJob(
        actor_user_id=1,
        request_key="fresh",
        command="import",
        created_at=store.now() + timedelta(seconds=1),
    )
    db.execute.side_effect = lambda _: Result([db.job, queued, fresh])
    await store.reconcile_orphans(store.now())
    assert db.job.status == queued.status == "failed"
    assert fresh.status == "queued"
    db.execute.side_effect = [Result(), Result()]
    assert await store.claim_next() is None


@pytest.mark.asyncio
async def test_execution_stops_on_revocation_token_change_cancel_and_lease_loss(env):
    db, settings = env
    await store.check_job(db.job.id, store.fingerprint("token"))
    settings.global_github_token = "changed"
    with pytest.raises(PermissionError):
        await store.check_job(db.job.id, store.fingerprint("token"))
    db.job.cancel_requested = True
    with pytest.raises(asyncio.CancelledError):
        await store.check_job(db.job.id, store.fingerprint("token"))
    store.worker_lease.set("lost-lease")
    store.redis_manager.refresh_lock.return_value = False
    with pytest.raises(asyncio.CancelledError):
        await store.check_job(db.job.id, store.fingerprint("token"))


@pytest.mark.asyncio
async def test_insert_and_dependencies_commit_atomically_existing_entries_preserved(env):
    db, _ = env
    db.job.status = "running"
    db.execute.side_effect = [Result([db.job]), Result()]
    info = PluginAIInfo(model="model", installation=InstallationConfig())
    result = RepositoryAnalysis(
        is_plugin=True,
        title="New",
        description="Description",
        category="utility",
        framework="swiftly",
    )
    added = await store.insert_plugin(
        db.job.id,
        "https://github.com/a/b",
        "author",
        result,
        info,
        [2, 3],
        store.fingerprint("token"),
    )
    assert added == 15
    plugin = next(value for value in db.added if isinstance(value, MarketPlugin))
    assert plugin.dependencies == "2,3" and not plugin.ai_metadata["reviewed"]
    assert db.job.items[-1]["plugin_id"] == plugin.id
    assert db.commit.await_count == 1
    db.execute.side_effect = [Result([db.job]), Result([db.plugin])]
    assert (
        await store.insert_plugin(
            db.job.id,
            "https://github.com/a/b",
            "author",
            result,
            info,
            [],
            store.fingerprint("token"),
        )
        == 12
    )
    assert db.plugin.title == "Original" and db.commit.await_count == 1
    db.execute.side_effect = lambda _: Result([(12, "https://github.com/A/B.git/")])
    assert await store.existing_plugin("https://github.com/a/b") == 12
    assert await store.existing_plugin("https://github.com/a/c") is None


@pytest.mark.asyncio
async def test_admin_review_preserves_provenance_and_updates_layout(env):
    db, _ = env
    db.plugin.ai_metadata = PluginAIInfo(model="original").model_dump()
    proposed = PluginAIInfo(
        model="forged", reviewed=True, installation=InstallationConfig(target_path="addons/Plugin")
    )
    reviewed = await store.review_plugin(12, 1, proposed)
    assert reviewed.model == "original" and reviewed.reviewed
    assert db.plugin.custom_install_path == "addons/Plugin"
    db.plugin.ai_metadata = None
    with pytest.raises(LookupError):
        await store.review_plugin(12, 1, proposed)


@pytest.mark.asyncio
async def test_worker_lifecycle_and_lost_lease_cancel_current_run(env, monkeypatch):
    db, _ = env
    monkeypatch.setattr(store, "reconcile_orphans", AsyncMock())
    monkeypatch.setattr(store, "claim_next", AsyncMock(return_value=store.snapshot(db.job)))
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(worker, "run_job", run)
    store.redis_manager.refresh_lock.side_effect = [True, False]
    instance = worker.PluginImportWorker()
    await asyncio.wait_for(instance.consume("lease"), timeout=3)
    assert started.is_set() and cancelled.is_set()
    monkeypatch.setattr(instance, "loop", AsyncMock())
    await instance.start()
    await asyncio.sleep(0)
    await instance.stop()
    assert instance.task is None
