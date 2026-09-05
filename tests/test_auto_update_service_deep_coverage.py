"""覆盖自动更新版本验证与调度条件的隔离分支。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import auto_update_service as auto_update


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.commits = 0
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return _Rows(self.rows)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _item):
        return None

    async def get(self, _model, _item_id):
        return None

    def add(self, item):
        self.added.append(item)
        if hasattr(item, "id"):
            item.id = 1


def _server(**overrides):
    values = {
        "id": 3,
        "name": "server",
        "last_update_check": None,
        "update_check_interval_hours": 1,
        "should_skip_background_checks": lambda: False,
        "current_game_version": "1.2.3.4",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_version_resolution_and_deadlines(monkeypatch):
    service = auto_update.AutoUpdateService()
    assert service._versions_match("1.2.3.4", "1234")
    assert not service._versions_match(None, "1234")
    assert not service._versions_match("1.2", "1234")
    server = _server()
    progress = AsyncMock()

    assert await service._resolve_observed_version(
        server, "1.2.3.4", "1234", None, {}, progress
    ) == (True, "1234", "1234")
    monkeypatch.setattr(
        auto_update.steam_api_service,
        "check_version",
        AsyncMock(return_value=(False, None)),
    )
    assert (await service._resolve_observed_version(server, "old", "1234", None, {}, progress))[
        0
    ] is False
    check = AsyncMock(return_value=(True, {"up_to_date": False, "required_version": "new"}))
    monkeypatch.setattr(auto_update.steam_api_service, "check_version", check)
    assert await service._resolve_observed_version(server, "old", None, None, {}, progress) == (
        False,
        "new",
        "new",
    )
    check.return_value = (True, {"up_to_date": True})
    assert await service._resolve_observed_version(server, "old2", None, None, {}, progress) == (
        True,
        None,
        None,
    )
    check.return_value = (False, None)
    assert await service._resolve_observed_version(server, "old3", None, None, {}, progress) == (
        False,
        None,
        None,
    )
    check.side_effect = asyncio.TimeoutError
    deadline = asyncio.get_running_loop().time() + 10
    assert not (
        await service._resolve_observed_version(server, "old4", None, deadline, {}, progress)
    )[0]
    expired = asyncio.get_running_loop().time() - 1
    assert await service._read_version_with_deadline(server, expired) == (False, None)
    monkeypatch.setattr(
        auto_update.steam_inf_service, "refresh_version_cache", AsyncMock(return_value=(True, "v"))
    )
    assert await service._read_version_with_deadline(server, None) == (True, "v")


@pytest.mark.asyncio
async def test_wait_for_updated_version_all_terminal_paths(monkeypatch):
    service = auto_update.AutoUpdateService()
    server = _server()
    progress = AsyncMock()
    service.VERSION_VERIFICATION_TIMEOUT_SECONDS = 0
    monkeypatch.setattr(
        service, "_read_version_with_deadline", AsyncMock(return_value=(False, None))
    )
    assert await service._wait_for_updated_version(server, "target", progress) == (
        False,
        None,
        None,
    )
    service.VERSION_VERIFICATION_TIMEOUT_SECONDS = 1
    service.VERSION_VERIFICATION_POLL_INTERVAL_SECONDS = 1
    monkeypatch.setattr(
        service, "_read_version_with_deadline", AsyncMock(side_effect=asyncio.TimeoutError)
    )
    assert (await service._wait_for_updated_version(server, "target", progress))[0] is False
    monkeypatch.setattr(
        service,
        "_read_version_with_deadline",
        AsyncMock(return_value=(True, "1.2.3.4")),
    )
    monkeypatch.setattr(
        auto_update.steam_api_service,
        "check_version",
        AsyncMock(return_value=(True, {"up_to_date": False, "required_version": "1234"})),
    )
    monkeypatch.setattr(auto_update.asyncio, "sleep", AsyncMock())
    result = await service._wait_for_updated_version(server, "target", progress)
    assert result[0] is True
    service.VERSION_VERIFICATION_TIMEOUT_SECONDS = 2
    monkeypatch.setattr(
        service, "_read_version_with_deadline", AsyncMock(return_value=(True, "old"))
    )
    monkeypatch.setattr(
        auto_update.steam_api_service, "check_version", AsyncMock(return_value=(False, None))
    )
    assert (await service._wait_for_updated_version(server, "target", progress))[0] is False


@pytest.mark.asyncio
async def test_check_and_update_servers_filters_and_single_checks(monkeypatch):
    service = auto_update.AutoUpdateService()
    server = _server(id=1)
    updating = _server(id=2)
    down = _server(id=3, should_skip_background_checks=lambda: True)
    recent = _server(id=4, last_update_check="recent")
    servers = [server, updating, down, recent]
    service.updating_servers.add(updating.id)
    db = _Db()
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    monkeypatch.setattr(
        "modules.models.Server.get_all_with_auto_update", AsyncMock(return_value=servers)
    )
    monkeypatch.setattr(
        auto_update.steam_api_service,
        "should_check_version",
        lambda last, interval: last != "recent",
    )
    check = AsyncMock()
    monkeypatch.setattr(service, "_check_and_update_server", check)
    await service._check_and_update_servers()
    check.assert_awaited_once_with(server)
    db.execute = AsyncMock(side_effect=RuntimeError("list failed"))
    await service._check_and_update_servers()

    service = auto_update.AutoUpdateService()
    service._trigger_server_update = AsyncMock()
    db = _Db()
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    monkeypatch.setattr(
        auto_update.steam_inf_service,
        "get_version_from_steam_inf",
        AsyncMock(return_value=(False, None)),
    )
    monkeypatch.setattr(
        auto_update.steam_api_service,
        "check_version",
        AsyncMock(return_value=(True, {"up_to_date": True})),
    )
    await service._check_and_update_server(server)
    assert not service._trigger_server_update.await_args_list
    monkeypatch.setattr(
        auto_update.steam_inf_service,
        "get_version_from_steam_inf",
        AsyncMock(return_value=(False, None)),
    )
    server.current_game_version = None
    await service._check_and_update_server(server)
    monkeypatch.setattr(
        auto_update.steam_inf_service,
        "get_version_from_steam_inf",
        AsyncMock(return_value=(True, "old")),
    )
    monkeypatch.setattr(
        auto_update.steam_api_service,
        "check_version",
        AsyncMock(return_value=(True, {"up_to_date": False, "required_version": "new"})),
    )
    await service._check_and_update_server(server)
    service._trigger_server_update.assert_awaited_once()

    def timeout_wait(awaitable, _timeout=None, **_kwargs):
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(auto_update.asyncio, "wait_for", timeout_wait)
    await service._check_and_update_server(server)


@pytest.mark.asyncio
async def test_update_guards_wait_cache_and_loop(monkeypatch):
    service = auto_update.AutoUpdateService()
    server = _server()
    monkeypatch.setattr(
        "services.plugin_diagnostic_service.has_diagnostic_blocker", AsyncMock(return_value=True)
    )
    assert await service._can_trigger_update(server) is False
    monkeypatch.setattr(
        "services.plugin_diagnostic_service.has_diagnostic_blocker", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        auto_update.maintenance_lock_service, "is_locked", AsyncMock(return_value=True)
    )
    assert await service._can_trigger_update(server) is False
    monkeypatch.setattr(
        auto_update.maintenance_lock_service, "is_locked", AsyncMock(return_value=False)
    )
    assert await service._can_trigger_update(server) is True

    service.running = True
    monkeypatch.setattr(
        service, "_check_and_update_servers", AsyncMock(side_effect=RuntimeError("check"))
    )
    monkeypatch.setattr(auto_update.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await service._update_loop()
    service = auto_update.AutoUpdateService()
    monkeypatch.setattr(service, "_update_loop", AsyncMock())
    await service.start()
    await service.stop()
