"""A failing fleet member must not starve independent background work."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from services.a2s_cache_service import A2SCacheService
from services.auto_update_service import AutoUpdateService
from services.discord_bot_manager import DiscordBotManager
from services.disk_space_service import DiskSpaceService
from services.host_system_info_service import HostSystemInfoService
from services.steam_inf_service import SteamInfService


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.active = False

    async def __aenter__(self):
        self.active = True
        return self

    async def __aexit__(self, *_args):
        self.active = False

    async def execute(self, _query):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.rows))


def _servers():
    return [
        SimpleNamespace(
            id=identifier,
            name=f"server-{identifier}",
            host=f"host-{identifier}",
            ssh_port=22,
            last_update_check=None,
            update_check_interval_hours=1,
            should_skip_background_checks=Mock(return_value=False),
        )
        for identifier in (7, 8)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["skip", "due", "check"])
async def test_game_update_retries_failed_member_and_checks_later_servers(
    monkeypatch, caplog, stage
):
    service = AutoUpdateService()
    servers = _servers()
    session = _Session(servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)
    monkeypatch.setattr(
        "modules.models.Server.get_all_with_auto_update", AsyncMock(return_value=servers)
    )
    due = Mock(return_value=True)
    monkeypatch.setattr("services.auto_update_service.steam_api_service.should_check_version", due)

    async def check(server):
        assert not session.active
        if stage == "check" and server.id == 7:
            raise RuntimeError("version service unavailable")

    checker = AsyncMock(side_effect=check)
    monkeypatch.setattr(service, "_check_and_update_server", checker)
    if stage == "skip":
        servers[0].should_skip_background_checks.side_effect = ValueError("invalid timestamp")
    elif stage == "due":
        due.side_effect = [ValueError("invalid timestamp"), True] * 2

    await service._check_and_update_servers()
    await service._check_and_update_servers()
    assert checker.await_args_list.count(call(servers[1])) == 2
    assert all(server.last_update_check is None for server in servers)
    assert "server 7" in caplog.text

    checker.reset_mock()
    servers[0].should_skip_background_checks.side_effect = None
    due.side_effect = None
    checker.side_effect = None
    await service._check_and_update_servers()
    assert checker.await_args_list == [call(server) for server in servers]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["a2s", "steam_inf"])
@pytest.mark.parametrize("stage", ["skip", "probe"])
async def test_background_probe_isolates_failures_before_and_during_probe(
    monkeypatch, caplog, kind, stage
):
    service = A2SCacheService() if kind == "a2s" else SteamInfService()
    servers = _servers()
    session = _Session(servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)
    probed = []

    async def probe(server, **_kwargs):
        assert not session.active
        if stage == "probe" and server.id == 7:
            raise RuntimeError("probe unavailable")
        await asyncio.sleep(0)
        probed.append(server.id)
        return True, "version"

    method = "_query_and_cache_server" if kind == "a2s" else "get_version_from_steam_inf"
    monkeypatch.setattr(service, method, probe)
    if stage == "skip":
        servers[0].should_skip_background_checks.side_effect = ValueError("invalid timestamp")
    scan = service._query_all_servers if kind == "a2s" else service._periodic_refresh_all
    await scan()
    await scan()
    assert probed == [8, 8]
    assert "server 7" in caplog.text
    if kind == "a2s":
        assert service.limiter_snapshot()["borrowers"] == 0

    servers[0].should_skip_background_checks.side_effect = None
    monkeypatch.setattr(service, method, AsyncMock(return_value=(True, "version")))
    await scan()
    assert getattr(service, method).await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["disk", "host", "a2s"])
async def test_foreground_probe_batch_keeps_order_and_other_results(monkeypatch, kind):
    servers = _servers()
    if kind == "disk":
        service = DiskSpaceService()
        method = service.get_many_disk_space
        probe_name = "get_disk_space"
        result = True, {"used_gb": 8}
    elif kind == "host":
        service = HostSystemInfoService()
        method = service.get_many_host_system_info
        probe_name = "get_host_system_info"
        result = {"server_id": 8, "success": True}
    else:
        service = A2SCacheService()
        method = service.get_many_cached_info
        probe_name = "_query_and_cache_server"
        result = None
        monkeypatch.setattr(
            importlib.import_module("services.a2s_cache_service").redis_manager,
            "get_many",
            AsyncMock(return_value=[None, {"success": True}]),
        )

    async def probe(server, **_kwargs):
        if server.id == 7:
            raise RuntimeError("one probe failed")
        await asyncio.sleep(0)
        return result

    monkeypatch.setattr(service, probe_name, probe)
    values = await method(servers, force_refresh=True)
    assert len(values) == 2
    if kind == "host":
        assert values[0]["server_id"] == 7 and values[0]["success"] is False
        assert values[1] == result
    elif kind == "disk":
        assert values == [None, result[1]]
    else:
        assert values == [None, {"success": True}]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_stage", ["skip", "probe"])
@pytest.mark.parametrize("kind", ["game_update", "a2s", "steam_inf"])
async def test_background_batch_cancellation_propagates(monkeypatch, kind, cancel_stage):
    servers = _servers()
    session = _Session(servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)
    if kind == "game_update":
        service = AutoUpdateService()
        monkeypatch.setattr(
            "modules.models.Server.get_all_with_auto_update", AsyncMock(return_value=servers)
        )
        monkeypatch.setattr(
            "services.auto_update_service.steam_api_service.should_check_version",
            Mock(return_value=True),
        )
        probe = "_check_and_update_server"
        scan = service._check_and_update_servers
    elif kind == "a2s":
        service = A2SCacheService()
        probe = "_query_and_cache_server"
        scan = service._query_all_servers
    else:
        service = SteamInfService()
        probe = "get_version_from_steam_inf"
        scan = service._periodic_refresh_all
    if cancel_stage == "skip":
        servers[0].should_skip_background_checks.side_effect = asyncio.CancelledError
        monkeypatch.setattr(service, probe, AsyncMock(return_value=(True, "version")))
    else:
        monkeypatch.setattr(service, probe, AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await scan()


@pytest.mark.asyncio
async def test_discord_reconciliation_continues_and_retries_failed_account(monkeypatch, caplog):
    manager = DiscordBotManager()
    session = _Session([7, 8])
    monkeypatch.setattr("services.discord_bot_manager.async_session_maker", lambda: session)

    async def reconcile(user_id):
        assert not session.active
        if user_id == 7:
            raise RuntimeError("account unavailable")

    reconcile_user = AsyncMock(side_effect=reconcile)
    monkeypatch.setattr(manager, "reconcile_user", reconcile_user)
    await manager.reconcile_all()
    await manager.reconcile_all()
    assert reconcile_user.await_args_list.count(call(8)) == 2
    assert "user 7" in caplog.text
    reconcile_user.reset_mock()
    reconcile_user.side_effect = None
    await manager.reconcile_all()
    assert {args.args[0] for args in reconcile_user.await_args_list} == {7, 8}


@pytest.mark.asyncio
async def test_discord_shutdown_continues_after_account_failure(monkeypatch, caplog):
    manager = DiscordBotManager()
    manager._runtimes = {7: SimpleNamespace(), 8: SimpleNamespace()}
    stopper = AsyncMock(side_effect=[RuntimeError("close failed"), None])
    monkeypatch.setattr(manager, "_stop_runtime", stopper)
    await manager.stop()
    assert stopper.await_args_list == [call(7, status="disabled"), call(8, status="disabled")]
    assert "user 7" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["reconcile", "stop"])
async def test_discord_batch_cancellation_propagates(monkeypatch, action):
    manager = DiscordBotManager()
    if action == "reconcile":
        monkeypatch.setattr(
            "services.discord_bot_manager.async_session_maker", lambda: _Session([7, 8])
        )
        monkeypatch.setattr(
            manager, "reconcile_user", AsyncMock(side_effect=asyncio.CancelledError)
        )
        operation = manager.reconcile_all
    else:
        manager._runtimes = {7: SimpleNamespace(), 8: SimpleNamespace()}
        monkeypatch.setattr(manager, "_stop_runtime", AsyncMock(side_effect=asyncio.CancelledError))
        operation = manager.stop
    with pytest.raises(asyncio.CancelledError):
        await operation()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["disk", "host", "a2s"])
async def test_foreground_batch_cancellation_drains_siblings(monkeypatch, kind):
    servers = _servers()
    entered = asyncio.Event()
    released = asyncio.Event()
    if kind == "disk":
        service = DiskSpaceService()
        method, probe_name = service.get_many_disk_space, "get_disk_space"
    elif kind == "host":
        service = HostSystemInfoService()
        method, probe_name = service.get_many_host_system_info, "get_host_system_info"
    else:
        service = A2SCacheService()
        method, probe_name = service.get_many_cached_info, "_query_and_cache_server"

    async def probe(server, **_kwargs):
        if server.id == 7:
            await entered.wait()
            raise asyncio.CancelledError
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    monkeypatch.setattr(service, probe_name, probe)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(method(servers, force_refresh=True), timeout=1)
    assert released.is_set()
    if kind == "a2s":
        assert service.limiter_snapshot()["borrowers"] == 0


@pytest.mark.asyncio
async def test_ssh_monitor_child_cancellation_drains_other_checks(monkeypatch):
    from services.ssh_health_monitor import SSHHealthMonitor

    service = SSHHealthMonitor()
    servers = _servers()
    session = _Session(servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)
    entered, released = asyncio.Event(), asyncio.Event()

    async def check(server):
        assert not session.active
        if server.id == 7:
            await entered.wait()
            raise asyncio.CancelledError
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    monkeypatch.setattr(service, "_check_server_health", check)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(service._check_all_servers(), timeout=1)
    assert released.is_set()


@pytest.mark.asyncio
async def test_discord_failed_client_close_still_releases_owned_resources(monkeypatch):
    from services.discord_bot_manager import _Runtime

    manager = DiscordBotManager()
    entered, released = asyncio.Event(), asyncio.Event()

    async def gateway():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    client_task = asyncio.create_task(gateway())
    renew_task = asyncio.create_task(asyncio.Event().wait())
    await entered.wait()
    client = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed")))
    manager._runtimes[7] = _Runtime(client, "f", "b", "lease", client_task, renew_task)
    release = AsyncMock()
    monkeypatch.setattr("services.discord_bot_manager.redis_manager.release_lock", release)
    with pytest.raises(RuntimeError, match="close failed"):
        await manager._stop_runtime(7, status="disabled")
    assert released.is_set()
    assert client_task.done() and renew_task.done()
    release.assert_awaited_once_with("discord_gateway:user:7", "lease")


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["execute", "commit"])
async def test_schedule_recalculation_uses_independent_transactions(monkeypatch, caplog, stage):
    from sqlalchemy.exc import PendingRollbackError

    from services import scheduled_task_service as module

    tasks = [SimpleNamespace(id=identifier) for identifier in (7, 8)]
    sessions = []
    committed = []
    failing = True

    class WriteSession(_Session):
        async def execute(self, statement):
            if getattr(self, "poisoned", False):
                raise PendingRollbackError("transaction must be rolled back")
            if statement.is_select:
                return await super().execute(statement)
            self.identifier = statement.compile().params["id_1"]
            if failing and self.identifier == 7 and stage == "execute":
                self.poisoned = True
                raise RuntimeError("one update failed")

        async def commit(self):
            if failing and self.identifier == 7 and stage == "commit":
                self.poisoned = True
                raise RuntimeError("one commit failed")
            committed.append(self.identifier)

    def factory():
        session = WriteSession(tasks)
        sessions.append(session)
        return session

    monkeypatch.setattr(module, "async_session_maker", factory)
    service = module.ScheduledTaskService()
    monkeypatch.setattr(service, "_calculate_next_run", lambda _task: module.get_current_time())
    await service._calculate_all_next_runs()
    await service._calculate_all_next_runs()
    assert committed == [8, 8]
    assert len(sessions) == 6
    assert "task 7" in caplog.text
    assert all(not session.active for session in sessions)
    failing = False
    await service._calculate_all_next_runs()
    assert committed[-2:] == [7, 8]


@pytest.mark.asyncio
async def test_schedule_recalculation_cancellation_does_not_continue(monkeypatch):
    from services import scheduled_task_service as module

    monkeypatch.setattr(
        module,
        "async_session_maker",
        lambda: _Session([SimpleNamespace(id=7), SimpleNamespace(id=8)]),
    )
    service = module.ScheduledTaskService()
    calculate = Mock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(service, "_calculate_next_run", calculate)
    with pytest.raises(asyncio.CancelledError):
        await service._calculate_all_next_runs()
    assert calculate.call_count == 1


@pytest.mark.asyncio
async def test_partial_batch_construction_failure_drains_created_children():
    from services.telemetry_runtime import collect_ordered

    child = asyncio.create_task(asyncio.Event().wait())

    def jobs():
        yield child
        raise RuntimeError("batch construction failed")

    with pytest.raises(RuntimeError, match="batch construction failed"):
        await collect_ordered(jobs())
    assert child.done() and child.cancelled()
