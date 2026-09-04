"""Isolated coverage for server monitoring endpoints."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.servers import monitoring


class _Session:
    def __init__(self, servers):
        self.servers = servers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.servers))


def _server(**overrides):
    values = {
        "id": 1,
        "host": "game.example",
        "game_port": 27015,
        "a2s_query_host": None,
        "a2s_query_port": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_monitoring_logs_success_and_redis_failure(monkeypatch):
    monkeypatch.setattr(monitoring, "get_server_with_permission", AsyncMock(return_value=_server()))
    redis = SimpleNamespace(get_monitoring_logs=AsyncMock(return_value=[{"event": "start"}]))
    monkeypatch.setattr(importlib.import_module("services.redis_manager"), "redis_manager", redis)
    result = await monitoring.get_monitoring_logs(
        1, limit=5, event_type="lifecycle", db=None, current_user=SimpleNamespace(id=1)
    )
    assert result == [{"event": "start"}]
    redis.get_monitoring_logs.assert_awaited_once_with(
        server_id=1, event_type="lifecycle", limit=5
    )

    redis.get_monitoring_logs.side_effect = RuntimeError("redis offline")
    assert (
        await monitoring.get_monitoring_logs(
            1, db=None, current_user=SimpleNamespace(id=1)
        )
        == []
    )


@pytest.mark.asyncio
async def test_monitoring_ping_cache_test_and_all_cache_paths(monkeypatch):
    assert await monitoring.ping() == {"status": "ok", "message": "pong"}
    test_result = await monitoring.test_a2s_cache()
    assert test_result["status"] == "ok"
    assert test_result["note"]

    servers = [_server(id=1), _server(id=2)]
    session = _Session(servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)
    cache = SimpleNamespace(
        get_cached_info=AsyncMock(side_effect=[{"success": True, "players": 2}, None])
    )
    monkeypatch.setattr(
        importlib.import_module("services.a2s_cache_service"), "a2s_cache_service", cache
    )
    result = await monitoring.get_all_servers_a2s_cache()
    assert result["servers"] == {"1": {"success": True, "players": 2}}
    assert "error" not in result

    cache.get_cached_info.side_effect = [RuntimeError("bad cache"), {"success": False}]
    result = await monitoring.get_all_servers_a2s_cache()
    assert result["servers"]["1"]["error"] == "Cache unavailable"
    assert result["servers"]["2"] == {"success": False}

    monkeypatch.setattr("modules.database.async_session_maker", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    result = await monitoring.get_all_servers_a2s_cache()
    assert result["error"] == "db down"


@pytest.mark.asyncio
async def test_server_a2s_info_uses_fallbacks_and_player_success_state(monkeypatch):
    server = _server()
    monkeypatch.setattr(monitoring, "get_server_with_permission", AsyncMock(return_value=server))
    service = SimpleNamespace(
        query_server_info=AsyncMock(return_value=(True, {"name": "CS2"})),
        query_players=AsyncMock(return_value=(True, [{"name": "player"}])),
    )
    monkeypatch.setattr(importlib.import_module("services.a2s_query"), "a2s_service", service)
    result = await monitoring.get_server_a2s_info(1, db=None, current_user=SimpleNamespace(id=1))
    assert result["query_host"] == "game.example"
    assert result["query_port"] == 27015
    assert result["players"] == [{"name": "player"}]

    server = _server(a2s_query_host="query.example", a2s_query_port=27016)
    monitoring.get_server_with_permission.return_value = server
    service.query_server_info.return_value = (True, {"name": "CS2"})
    service.query_players.return_value = (False, [{"name": "hidden"}])
    result = await monitoring.get_server_a2s_info(1, db=None, current_user=SimpleNamespace(id=1))
    assert result["query_host"] == "query.example"
    assert result["query_port"] == 27016
    assert result["players"] == []

    service.query_server_info.return_value = (False, {"error": "offline"})
    result = await monitoring.get_server_a2s_info(1, db=None, current_user=SimpleNamespace(id=1))
    assert result["success"] is False
    assert result["players"] == []
    assert service.query_players.await_count == 2
