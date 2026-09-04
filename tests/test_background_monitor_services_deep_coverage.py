"""覆盖 Steam.inf、A2S 和 SSH 健康监控的后台边界。"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest

from services import ssh_health_monitor as health
from services import steam_inf_service as steam

a2s = importlib.import_module("services.a2s_cache_service")


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, *, rows=(), server=None, execute_error=None):
        self.rows = list(rows)
        self.server = server
        self.execute_error = execute_error
        self.commits = 0
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement):
        if self.execute_error:
            raise self.execute_error
        self.statements.append(statement)
        return _Rows(self.rows)

    async def get(self, _model, _server_id):
        return self.server

    async def commit(self):
        self.commits += 1


def _server(**overrides):
    values = {
        "id": 3,
        "name": "server",
        "host": "127.0.0.1",
        "game_port": 27015,
        "a2s_query_host": None,
        "a2s_query_port": None,
        "current_game_version": "old",
        "game_directory": "/srv/cs2",
        "should_skip_background_checks": lambda: False,
        "enable_ssh_health_monitoring": True,
        "ssh_health_check_interval_hours": 2,
        "last_ssh_health_check": None,
        "ssh_health_status": "healthy",
        "consecutive_ssh_failures": 0,
        "ssh_health_failure_threshold": 4,
        "is_ssh_down": False,
        "is_password_auth": False,
        "is_key_auth": True,
        "ssh_port": 22,
        "ssh_user": "root",
        "ssh_password": "secret",
        "ssh_key_path": "/tmp/key",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_steam_inf_parsing_cache_and_file_paths(monkeypatch):
    assert steam._optional_text(None) is None
    assert steam._optional_text(b" 42 ") == "42"
    assert steam._optional_text(" ") is None
    assert steam.parse_steam_inf_fields("PatchVersion=1.2.3.4\nClientVersion=55") == (
        "1.2.3.4",
        "55",
    )
    assert steam.parse_steam_inf_fields("ServerVersion=66") == (None, "66")
    assert steam.parse_steam_inf_fields("") == (None, None)
    assert steam.SteamInfService()._parse_patch_version("PatchVersion=1.0.0.1") == "1.0.0.1"

    service = steam.SteamInfService()
    server = _server()
    redis = SimpleNamespace(
        get=AsyncMock(side_effect=[b"1.2.3.4", b"99"]),
        set=AsyncMock(),
        delete=AsyncMock(),
    )
    monkeypatch.setattr(steam, "redis_manager", redis)
    assert await service.get_steam_inf_details(server) == (True, "1.2.3.4", "99")

    redis.get = AsyncMock(return_value=None)
    reader = AsyncMock(return_value=(True, "2.0.0.0", "100"))
    monkeypatch.setattr(service, "_read_version_from_file", reader)
    assert await service.get_version_from_steam_inf(server) == (True, "2.0.0.0")
    redis.set.assert_awaited()

    reader.return_value = (True, "3.0.0.0", None)
    assert await service.get_steam_inf_details(server, force_refresh=True) == (
        True,
        "3.0.0.0",
        None,
    )
    redis.delete.assert_awaited()
    reader.return_value = (False, None, None)
    assert await service.get_steam_inf_details(server, force_refresh=True) == (False, None, None)

    class _Conn:
        def __init__(self):
            self.commands = []

        async def connect(self, _server):
            return True, "ok"

        async def execute_command(self, command):
            self.commands.append(command)
            if "test -f" in command:
                return True, "exists", ""
            return True, "PatchVersion=4.0.0.1\nServerVersion=101", ""

        async def disconnect(self):
            return None

    connection = _Conn()
    monkeypatch.setattr(steam, "_ssh_manager_factory", lambda: connection)
    file_service = steam.SteamInfService()
    assert await file_service._read_version_from_file(server) == (True, "4.0.0.1", "101")


@pytest.mark.asyncio
async def test_steam_inf_read_failures_refresh_and_periodic(monkeypatch):
    server = _server()
    service = steam.SteamInfService()

    class _Fail:
        def __init__(self, mode):
            self.mode = mode
            self.disconnect = AsyncMock()

        async def connect(self, _server):
            return (False, "offline") if self.mode == "connect" else (True, "ok")

        async def execute_command(self, command):
            if self.mode == "missing":
                return True, "missing", ""
            if self.mode == "check_error":
                return False, "", "check failed"
            if self.mode == "read_error":
                return False, "", "read failed"
            return True, "PatchVersion=not-a-version", ""

    for mode in ("connect", "missing", "check_error", "read_error", "parse"):
        worker = _Fail(mode)
        monkeypatch.setattr(steam, "_ssh_manager_factory", lambda worker=worker: worker)
        assert await service._read_version_from_file(server) == (False, None, None)
        worker.disconnect.assert_awaited_once()
    monkeypatch.setattr(steam, "_ssh_manager_factory", None)
    assert await service._read_version_from_file(server) == (False, None, None)

    worker = _Fail("missing")
    monkeypatch.setattr(steam, "_ssh_manager_factory", lambda: worker)
    def timeout_wait(awaitable, _timeout=None, **_kwargs):
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(steam.asyncio, "wait_for", timeout_wait)
    assert await service._read_version_from_file(server, timeout=0.1) == (False, None, None)
    monkeypatch.undo()

    redis = SimpleNamespace(delete=AsyncMock(), set=AsyncMock())
    monkeypatch.setattr(steam, "redis_manager", redis)
    service.get_version_from_steam_inf = AsyncMock(return_value=(True, "5.0.0.1"))
    db = _Db()
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    assert await service.refresh_version_cache(server) == (True, "5.0.0.1")
    assert db.commits == 1
    service.get_version_from_steam_inf.return_value = (False, None)
    assert await service.refresh_version_cache(server) == (False, None)
    await service.clear_version_cache(server.id)
    assert redis.delete.await_count == 2

    servers = [_server(id=1), _server(id=2, should_skip_background_checks=lambda: True)]
    db = _Db(rows=servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    service.get_version_from_steam_inf = AsyncMock(return_value=(True, "6.0.0.1"))
    await service._periodic_refresh_all()
    assert service.get_version_from_steam_inf.await_count == 1
    monkeypatch.setattr(steam.asyncio, "wait_for", timeout_wait)
    await service._periodic_refresh_all()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))
    await service._periodic_refresh_all()


@pytest.mark.asyncio
async def test_steam_inf_lifecycle_and_db_update_error(monkeypatch):
    service = steam.SteamInfService()
    monkeypatch.setattr(service, "_refresh_loop", AsyncMock())
    await service.start()
    await asyncio.sleep(0)
    await service.start()
    await service.stop()
    loop_service = steam.SteamInfService()
    loop_service.running = True
    monkeypatch.setattr(
        loop_service,
        "_periodic_refresh_all",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(steam.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await loop_service._refresh_loop()

    server = _server()
    service.get_version_from_steam_inf = AsyncMock(return_value=(True, "7.0.0.1"))
    db = _Db(execute_error=RuntimeError("write failed"))
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    assert await service.refresh_version_cache(server) == (True, "7.0.0.1")


@pytest.mark.asyncio
async def test_a2s_cache_loops_and_cache_version(monkeypatch):
    service = a2s.A2SCacheService()
    steam_api = SimpleNamespace(check_version=AsyncMock(return_value=(True, {"success": True, "required_version": "1.2", "message": "ok"})))
    monkeypatch.setattr("services.steam_api_service.steam_api_service", steam_api)
    redis = SimpleNamespace(set=AsyncMock(), get=AsyncMock(return_value={"version": "1.2"}))
    monkeypatch.setattr(a2s, "redis_manager", redis)
    await service._cache_steam_version()
    assert redis.set.await_count == 1
    steam_api.check_version.return_value = (True, {"success": False})
    await service._cache_steam_version()
    steam_api.check_version.side_effect = RuntimeError("steam down")
    await service._cache_steam_version()
    assert await service.get_latest_steam_version() == {"version": "1.2"}
    redis.get.return_value = "bad"
    assert await service.get_latest_steam_version() is None
    redis.get.side_effect = RuntimeError("redis down")
    assert await service.get_latest_steam_version() is None

    service.running = True
    monkeypatch.setattr(service, "_query_all_servers", AsyncMock(side_effect=RuntimeError("query")))
    monkeypatch.setattr(service, "_cache_steam_version", AsyncMock(side_effect=RuntimeError("version")))
    monkeypatch.setattr(a2s.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await service._query_loop()
    with pytest.raises(asyncio.CancelledError):
        await service._steam_version_loop()


@pytest.mark.asyncio
async def test_a2s_queries_success_failures_and_lifecycle(monkeypatch):
    service = a2s.A2SCacheService()
    server = _server(a2s_query_host="query.example", a2s_query_port=27020)
    info = {"version": "1.2.3.4", "server_name": "test", "player_count": 2, "max_players": 10}
    query = SimpleNamespace(
        query_server_info=AsyncMock(return_value=(True, info)),
        query_players=AsyncMock(return_value=(True, [{"name": "p"}])),
    )
    monkeypatch.setattr(a2s, "a2s_service", query)
    steam_api = SimpleNamespace(parse_version_from_a2s=lambda value: "new-version")
    monkeypatch.setattr("services.steam_api_service.steam_api_service", steam_api)
    redis = SimpleNamespace(set=AsyncMock(), get=AsyncMock())
    monkeypatch.setattr(a2s, "redis_manager", redis)
    db = _Db()
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    await service._query_and_cache_server(server)
    assert redis.set.await_count == 1 and db.commits == 1

    query.query_server_info.return_value = (False, None)
    await service._query_and_cache_server(server)
    query.query_server_info.side_effect = RuntimeError("udp error")
    await service._query_and_cache_server(server)
    assert redis.set.await_count == 3
    redis.set.side_effect = RuntimeError("redis error")
    await service._query_and_cache_server(server)

    service._query_and_cache_server = AsyncMock()
    servers = [_server(id=1), _server(id=2, should_skip_background_checks=lambda: True)]
    db = _Db(rows=servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    await service._query_all_servers(timeout=None)
    assert service._query_and_cache_server.await_count == 1
    original_gather = a2s.asyncio.gather

    def timeout_gather(*awaitables, **_kwargs):
        for awaitable in awaitables:
            awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(a2s.asyncio, "gather", timeout_gather)
    await service._query_all_servers(timeout=0.01)
    monkeypatch.setattr(a2s.asyncio, "gather", original_gather)
    db.execute = AsyncMock(side_effect=RuntimeError("db error"))
    await service._query_all_servers()

    service._query_and_cache_server = AsyncMock()
    redis.get = AsyncMock(side_effect=[{"success": True}, '{"success": false}', "bad", RuntimeError("x")])
    assert await service.get_cached_info(1) == {"success": True}
    assert await service.get_cached_info(1) == {"success": False}
    assert await service.get_cached_info(1) is None
    assert await service.get_cached_info(1) is None
    service._query_and_cache_server = AsyncMock()
    assert await service.refresh_cached_info(server) is None

    await service.start()
    await service.stop()


class _Conn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


@pytest.mark.asyncio
async def test_ssh_health_auth_due_and_lifecycle(monkeypatch):
    monitor = health.SSHHealthMonitor()
    now = datetime.now(timezone.utc)
    server = _server(last_ssh_health_check=now, ssh_health_status="healthy")
    assert monitor._check_due(server, now) is False
    server.last_ssh_health_check = now - timedelta(hours=3)
    monitor.last_check_times[server.id] = now
    assert monitor._check_due(server, now) is False
    monitor.last_check_times.clear()
    server.ssh_health_status = "completely_down"
    assert monitor._check_due(server, now) is False
    server.ssh_health_status = "healthy"
    server.last_ssh_health_check = datetime.now()
    assert monitor._check_due(server, now) is False

    conn = _Conn()
    monkeypatch.setattr(health.asyncssh, "connect", AsyncMock(return_value=conn))
    assert await monitor._test_ssh_connection(_server(is_password_auth=True, is_key_auth=False))
    assert conn.closed
    assert await monitor._test_ssh_connection(_server(is_password_auth=False, is_key_auth=True))
    assert await monitor._test_ssh_connection(_server(is_password_auth=False, is_key_auth=False)) is False
    monkeypatch.setattr(health.asyncssh, "connect", AsyncMock(side_effect=asyncio.TimeoutError))
    assert await monitor._test_ssh_connection(server) is False
    monkeypatch.setattr(health.asyncssh, "connect", AsyncMock(side_effect=asyncssh.PermissionDenied("no")))
    assert await monitor._test_ssh_connection(server) is False
    monkeypatch.setattr(health.asyncssh, "connect", AsyncMock(side_effect=RuntimeError("ssh")))
    assert await monitor._test_ssh_connection(server) is False

    monkeypatch.setattr(monitor, "_monitor_loop", AsyncMock())
    await monitor.start()
    await monitor.start()
    await monitor.stop()


@pytest.mark.asyncio
async def test_ssh_health_check_all_and_status_transitions(monkeypatch):
    monitor = health.SSHHealthMonitor()
    servers = [_server(id=1), _server(id=2)]
    db = _Db(rows=servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    monitor._check_server_health = AsyncMock(side_effect=[None, RuntimeError("one")])
    await monitor._check_all_servers()
    assert monitor._check_server_health.await_count == 2
    db.rows = []
    await monitor._check_all_servers()
    db.execute = AsyncMock(side_effect=RuntimeError("list error"))
    await monitor._check_all_servers()

    monitor = health.SSHHealthMonitor()
    source = _server(ssh_health_status="degraded", consecutive_ssh_failures=1)
    db = _Db(server=source)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    monkeypatch.setattr(health, "get_current_time", lambda: datetime.now(timezone.utc))
    monitor.last_check_times.clear()
    monitor._test_ssh_connection = AsyncMock(return_value=True)
    await monitor._check_server_health(source)
    assert db.commits == 1

    source = _server(ssh_health_status="healthy", consecutive_ssh_failures=0)
    db.server = source
    monitor.last_check_times.clear()
    monitor._test_ssh_connection.return_value = False
    await monitor._check_server_health(source)
    assert db.commits == 2
    source.consecutive_ssh_failures = 2
    db.server = source
    monitor.last_check_times.clear()
    await monitor._check_server_health(source)
    source.consecutive_ssh_failures = 3
    db.server = source
    monitor.last_check_times.clear()
    await monitor._check_server_health(source)
    source.consecutive_ssh_failures = 4
    db.server = source
    monitor.last_check_times.clear()
    await monitor._check_server_health(source)

    missing_db = _Db(server=None)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: missing_db)
    monitor.last_check_times.clear()
    await monitor._check_server_health(source)


@pytest.mark.asyncio
async def test_ssh_manual_reconnect_paths(monkeypatch):
    monitor = health.SSHHealthMonitor()
    server = _server()
    db = _Db(server=None)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: db)
    assert await monitor.manual_reconnect(9) == (False, "Server 9 not found")
    db.server = server
    monitor._test_ssh_connection = AsyncMock(return_value=False)
    assert await monitor.manual_reconnect(3) == (
        False,
        "SSH connection failed - server is still unreachable",
    )
    monitor._test_ssh_connection.return_value = True
    monitor.last_check_times[3] = datetime.now(timezone.utc)
    assert await monitor.manual_reconnect(3) == (
        True,
        "SSH connection successful - server health restored",
    )
