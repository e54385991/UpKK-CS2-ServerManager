"""Cover Discord Gateway lifecycle transitions with in-memory doubles."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules import User, UserDiscordBot
from services import discord_bot_manager as module
from services.discord_bot_manager import DiscordBotManager, _Runtime


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _DB:
    def __init__(self, *, bot=None, user=None, bindings=()):
        self.bot = bot
        self.user = user
        self.bindings = list(bindings)
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, _key):
        if model is UserDiscordBot:
            return self.bot
        if model is User:
            return self.user
        return None

    async def execute(self, _statement):
        return _Result(self.bindings)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _bot(**overrides):
    values = dict(
        user_id=4,
        enabled=True,
        token_encrypted="encrypted",
        message_trigger_mode="mention_only",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _client_task(*, done=True):
    task = asyncio.get_running_loop().create_future()
    if done:
        task.set_result(None)
    return task


@pytest.mark.asyncio
async def test_status_and_binding_persistence_helpers_cover_missing_and_rows(monkeypatch):
    manager = DiscordBotManager()
    db = _DB(bot=None)
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    await manager._update_bot_status(4, "disabled", "secret-token", connected=True)
    assert db.commits == 0

    bot = _bot()
    db = _DB(bot=bot)
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    monkeypatch.setattr(module, "get_current_time", lambda: "now")
    await manager._update_bot_status(4, "error", "secret-token", connected=True)
    assert bot.connection_status == "error"
    assert bot.last_error == "secret-token"
    assert bot.last_connected_at == "now"

    binding = SimpleNamespace(invalid_reason=None)
    db = _DB(bindings=[binding])
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    await manager._mark_guild_invalid(4, "10", "bad guild")
    assert binding.invalid_reason == "bad guild"
    binding.invalid_reason = "command_sync_failed"
    await manager._clear_guild_invalid(4, "10")
    assert binding.invalid_reason is None
    await manager._guild_removed(4, "10")
    assert binding.invalid_reason == "bot_not_in_guild"


@pytest.mark.asyncio
async def test_reconcile_user_covers_disabled_same_runtime_and_lease_failures(monkeypatch):
    manager = DiscordBotManager()
    manager._started = False
    await manager.reconcile_user(4)

    manager._started = True
    disabled = _DB(bot=_bot(enabled=False), user=SimpleNamespace(is_active=True), bindings=[])
    monkeypatch.setattr(module, "async_session_maker", lambda: disabled)
    manager._stop_runtime = AsyncMock()
    await manager.reconcile_user(4)
    manager._stop_runtime.assert_awaited_once_with(4, status="disabled")

    binding = SimpleNamespace(
        server_id=3,
        enabled=True,
        guild_id="10",
        channel_ids=[],
        role_ids=[],
        user_ids=[],
        capabilities=["status"],
        invalid_reason=None,
    )
    active_db = _DB(bot=_bot(), user=SimpleNamespace(is_active=True), bindings=[binding])
    monkeypatch.setattr(module, "async_session_maker", lambda: active_db)
    runtime = SimpleNamespace(
        fingerprint=module.hashlib.sha256(b"other").hexdigest(),
        binding_fingerprint="old",
        client=SimpleNamespace(sync_bound_guilds=AsyncMock()),
    )
    manager._runtimes[4] = runtime
    manager._stop_runtime = AsyncMock()
    monkeypatch.setattr(module, "decrypt_credential", lambda _value: "token")
    monkeypatch.setattr(module.redis_manager, "acquire_lock", AsyncMock(return_value=False))
    await manager.reconcile_user(4)
    manager._stop_runtime.assert_awaited_once_with(4, status="restarting")

    manager._runtimes.clear()
    monkeypatch.setattr(module.redis_manager, "acquire_lock", AsyncMock(return_value=None))
    manager._update_bot_status = AsyncMock()
    await manager.reconcile_user(4)
    manager._update_bot_status.assert_awaited_once_with(
        4, "redis_unavailable", "Redis lease unavailable; Gateway not started"
    )

    monkeypatch.setattr(module.redis_manager, "acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(module, "decrypt_credential", lambda _value: None)
    manager._update_bot_status = AsyncMock()
    await manager.reconcile_user(4)
    manager._update_bot_status.assert_awaited_once_with(4, "error", "Bot Token unavailable")


@pytest.mark.asyncio
async def test_reconcile_user_success_and_renew_lease_lost(monkeypatch):
    manager = DiscordBotManager()
    manager._started = True
    db = _DB(bot=_bot(), user=SimpleNamespace(is_active=True), bindings=[])
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    monkeypatch.setattr(module, "decrypt_credential", lambda _value: "token")
    monkeypatch.setattr(module.redis_manager, "acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(
        module,
        "ManagedDiscordClient",
        lambda *_args, **_kwargs: SimpleNamespace(start=lambda *_a, **_k: None),
    )
    renew_method = manager._renew_lease

    class _FakeTask:
        def add_done_callback(self, _callback):
            return None

        def cancel(self):
            return True

    monkeypatch.setattr(module.asyncio, "create_task", lambda _coro: _FakeTask())
    monkeypatch.setattr(manager, "_renew_lease", lambda *_args: None)
    manager._update_bot_status = AsyncMock()
    await manager.reconcile_user(4)
    assert 4 in manager._runtimes
    monkeypatch.setattr(manager, "_renew_lease", renew_method)
    manager._started = True
    monkeypatch.setattr(module.asyncio, "sleep", _turn_off(manager))
    runtime = SimpleNamespace(lease_token="lease", client=SimpleNamespace(close=AsyncMock()))
    manager._runtimes[4] = runtime
    monkeypatch.setattr(module.redis_manager, "refresh_lock", AsyncMock(return_value=False))
    manager._update_bot_status = AsyncMock()
    await manager._renew_lease(4, "lease")
    runtime.client.close.assert_awaited_once()
    manager._update_bot_status.assert_awaited_once_with(4, "lease_lost", "Redis lease lost")


def _turn_off(manager):
    async def sleep(_seconds):
        manager._started = False

    return sleep


@pytest.mark.asyncio
async def test_runtime_cleanup_client_stop_and_lifecycle_loops(monkeypatch):
    manager = DiscordBotManager()
    manager._update_bot_status = AsyncMock()
    await manager._stop_runtime(4, status="disabled")
    manager._update_bot_status.assert_awaited_once_with(4, "disabled", None)

    renew = _client_task()
    client_task = _client_task()
    client = SimpleNamespace(close=AsyncMock())
    manager._runtimes[4] = _Runtime(client, "f", "b", "lease", client_task, renew)
    monkeypatch.setattr(module.redis_manager, "release_lock", AsyncMock())
    await manager._stop_runtime(4, status="restarting")
    assert 4 not in manager._runtimes
    client.close.assert_awaited_once()

    manager._started = True
    await manager._reconcile_loop.__wrapped__(manager) if hasattr(
        manager._reconcile_loop, "__wrapped__"
    ) else None


@pytest.mark.asyncio
async def test_client_stopped_and_reconcile_all_short_paths(monkeypatch):
    manager = DiscordBotManager()
    manager._update_bot_status = AsyncMock()
    unrelated = _client_task()
    await manager._client_stopped(4, unrelated)
    assert manager._update_bot_status.await_count == 0
    task = _client_task()
    renew = _client_task()
    manager._runtimes[4] = _Runtime(SimpleNamespace(), "f", "b", "lease", task, renew)
    monkeypatch.setattr(module.redis_manager, "release_lock", AsyncMock())
    await manager._client_stopped(4, task)
    manager._update_bot_status.assert_awaited_with(
        4, "disconnected", "Discord Gateway disconnected"
    )

    db = _DB()
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    manager._started = False
    manager._reconcile_task = None
    await manager.start()
    await manager.stop()
