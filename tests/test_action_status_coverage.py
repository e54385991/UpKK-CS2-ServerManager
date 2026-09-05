"""Coverage for SSH status and reconnect endpoints."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.routes.actions import status as routes


class _Db:
    def __init__(self):
        self.execute = AsyncMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()


def _server(**overrides):
    values = {
        "id": 4,
        "host": "game.example",
        "game_directory": "/srv/cs2",
        "is_ssh_down": False,
        "consecutive_ssh_failures": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _access(monkeypatch, server):
    monkeypatch.setattr(routes, "get_server_and_verify_ownership", AsyncMock(return_value=server))


@pytest.mark.asyncio
async def test_ssh_connection_info_and_reconnect_paths(monkeypatch):
    server = _server(is_ssh_down=True)
    _access(monkeypatch, server)
    pool = SimpleNamespace(
        get_connection_info=AsyncMock(return_value={"connected": True}),
        manual_reconnect=AsyncMock(return_value=(True, "connection", "reconnected")),
        release_connection=AsyncMock(),
    )
    pool_module = importlib.import_module("services.ssh_connection_pool")
    monkeypatch.setattr(pool_module, "ssh_connection_pool", pool)
    watch_module = importlib.import_module("services.steamcmd_watch")
    monkeypatch.setattr(watch_module, "maybe_resume_steamcmd_watch", AsyncMock())

    def discard_task(coroutine):
        coroutine.close()
        return SimpleNamespace()

    monkeypatch.setattr(routes.asyncio, "create_task", Mock(side_effect=discard_task))
    db = _Db()
    assert await routes.get_ssh_connection_info(4, db, SimpleNamespace(id=2)) == {"connected": True}
    result = await routes.reconnect_ssh(4, db, SimpleNamespace(id=2))
    assert result["success"] is True
    assert db.execute.await_count == 2
    pool.release_connection.assert_awaited_once_with(server, "connection")

    pool.manual_reconnect.return_value = (False, None, "still offline")
    assert (await routes.reconnect_ssh(4, _Db(), SimpleNamespace(id=2)))["success"] is False
    pool.manual_reconnect.side_effect = RuntimeError("pool failure")
    with pytest.raises(HTTPException) as caught:
        await routes.reconnect_ssh(4, _Db(), SimpleNamespace(id=2))
    assert caught.value.status_code == 500

    server.is_ssh_down = False
    pool.manual_reconnect.side_effect = None
    pool.manual_reconnect.return_value = (True, None, "ok")
    db = _Db()
    assert (await routes.reconnect_ssh(4, db, SimpleNamespace(id=2)))["success"] is True
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_reset_counter_and_metamod_cache_ssh_fallbacks(monkeypatch):
    server = _server()
    _access(monkeypatch, server)
    pool = SimpleNamespace(reset_reconnection_counter=AsyncMock())
    monkeypatch.setattr(
        importlib.import_module("services.ssh_connection_pool"), "ssh_connection_pool", pool
    )
    assert (await routes.reset_reconnect_counter(4, _Db(), SimpleNamespace(id=2)))[
        "success"
    ] is True
    pool.reset_reconnection_counter.side_effect = RuntimeError("reset failed")
    with pytest.raises(HTTPException) as caught:
        await routes.reset_reconnect_counter(4, _Db(), SimpleNamespace(id=2))
    assert caught.value.status_code == 500

    client = SimpleNamespace(
        get=AsyncMock(return_value='{"success": true, "installed": true, "path": "/x"}'),
        set=AsyncMock(),
    )
    redis = SimpleNamespace(prefixed_key=lambda key: f"prefix:{key}", client=client)
    monkeypatch.setattr(routes, "redis_manager", redis)
    cached = await routes.get_metamod_status(4, _Db(), SimpleNamespace(id=2))
    assert cached.installed is True

    client.get.side_effect = RuntimeError("cache down")
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(False, "offline")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(routes, "SSHManager", lambda: ssh)
    result = await routes.get_metamod_status(4, _Db(), SimpleNamespace(id=2))
    assert result.success is False
    assert "offline" in result.error

    client.get.side_effect = None
    client.get.return_value = None
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "connected")),
        execute_command=AsyncMock(return_value=(True, "exists", "")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(routes, "SSHManager", lambda: ssh)
    result = await routes.get_metamod_status(4, _Db(), SimpleNamespace(id=2))
    assert result.installed is True
    client.set.assert_awaited_once()
    await routes.get_metamod_status(4, _Db(), SimpleNamespace(id=2))

    client.get.return_value = None
    client.set.side_effect = RuntimeError("cache write failed")
    ssh.execute_command.return_value = (True, "missing", "")
    result = await routes.get_metamod_status(4, _Db(), SimpleNamespace(id=2))
    assert result.installed is False
    assert result.path is None

    ssh.execute_command.side_effect = RuntimeError("check failed")
    result = await routes.get_metamod_status(4, _Db(), SimpleNamespace(id=2))
    assert result.success is False
    assert "check failed" in result.error
    assert ssh.disconnect.await_count >= 3
