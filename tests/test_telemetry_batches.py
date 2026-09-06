"""Performance, cancellation and isolation contracts for telemetry batches."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.config import settings
from modules.models import Server
from services import telemetry_runtime
from services.a2s_cache_service import A2SCacheService
from services.concurrency_limiter import KeyedConcurrencyLimiter
from services.disk_space_service import DiskSpaceService
from services.host_system_info_service import HostSystemInfoService, parse_host_system_info
from services.redis_manager import RedisManager
from services.servers.telemetry import load_telemetry_servers
from services.steam_inf_service import SteamInfService

MODULES = [
    importlib.import_module(f"services.{name}")
    for name in (
        "disk_space_service",
        "host_system_info_service",
        "a2s_cache_service",
        "steam_inf_service",
    )
]


def server(identifier, host=None):
    return SimpleNamespace(
        id=identifier,
        host=host or f"host-{identifier}",
        ssh_port=22,
        game_directory="/srv/cs2",
        should_skip_background_checks=lambda: False,
    )


@pytest.fixture
def store(monkeypatch):
    manager = RedisManager.__new__(RedisManager)
    manager.client = SimpleNamespace(
        get=AsyncMock(return_value=None),
        mget=AsyncMock(),
        set=AsyncMock(return_value=True),
        delete=AsyncMock(return_value=1),
        eval=AsyncMock(return_value=1),
    )
    manager._coordination_retry_after = 0
    monkeypatch.setattr(settings, "REDIS_KEY_PREFIX", "telemetry-test")
    for module in MODULES:
        monkeypatch.setattr(module, "redis_manager", manager)
    monkeypatch.setattr(
        telemetry_runtime,
        "ssh_probe_limiter",
        KeyedConcurrencyLimiter(global_limit=4, per_key_limit=1),
    )
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 20, 100, 1000])
@pytest.mark.parametrize("kind", ["disk", "host", "a2s"])
async def test_cache_hits_are_one_mget_and_keep_input_order(
    count, kind, store, monkeypatch, caplog
):
    servers = [server(identifier) for identifier in reversed(range(count))]
    if kind == "disk":
        values = [{"used_gb": item.id} for item in servers]
        service = DiskSpaceService()
        method = service.get_many_disk_space
        monkeypatch.setattr(
            service, "_read_disk_space", AsyncMock(side_effect=AssertionError("SSH"))
        )
    elif kind == "host":
        values = [parse_host_system_info("PROBE_OK=1", item.id) for item in servers]
        service = HostSystemInfoService()
        method = service.get_many_host_system_info
        monkeypatch.setattr(service, "_collect", AsyncMock(side_effect=AssertionError("SSH")))
    else:
        values = [{"success": True, "server_info": {"player_count": item.id}} for item in servers]
        service = A2SCacheService()
        method = service.get_many_cached_info
        monkeypatch.setattr(
            service, "_query_and_cache_server", AsyncMock(side_effect=AssertionError("UDP"))
        )
    store.client.mget.return_value = [json.dumps(value) for value in values]
    with caplog.at_level(logging.DEBUG):
        result = await method(servers)
    expected = [{**value, "cached": True} for value in values] if kind == "host" else values
    assert result == expected
    assert store.client.mget.await_count == bool(count)
    assert store.client.get.await_count == 0
    if count:
        prefix = {"disk": "disk_space", "host": "host_system_info", "a2s": "a2s:server"}[kind]
        store.client.mget.assert_awaited_once_with(
            [f"telemetry-test:{prefix}:{item.id}" for item in servers]
        )
    assert f"count={count}" in caplog.text
    assert f"cache_hits={count}" in caplog.text
    assert "host-" not in caplog.text


@pytest.mark.asyncio
async def test_mget_matches_single_get_decoding_prefix_and_duplicates(store, caplog):
    raw = ['{"x": 1}', "null", "false", "0", "text", "", None, "[1]", '"value"']
    keys = ["repeat", "telemetry-test:repeat", *[f"key-{i}" for i in range(7)]]
    store.client.mget.return_value = raw
    store.client.get.side_effect = raw
    assert await store.get_many(keys) == [await store.get(key) for key in keys]
    assert store.client.mget.call_args.args[0][:2] == ["telemetry-test:repeat"] * 2
    store.client.mget.side_effect = RuntimeError("password=secret")
    assert await store.get_many(["one", "two"]) == [None, None]
    assert "password" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 20, 100, 1000])
async def test_missing_corrupt_and_failed_cache_reads_never_probe_by_default(
    store, monkeypatch, count
):
    servers = [server(i) for i in range(count)]
    disk = DiskSpaceService()
    a2s = A2SCacheService()
    monkeypatch.setattr(disk, "_read_disk_space", AsyncMock(side_effect=AssertionError("SSH")))
    monkeypatch.setattr(
        a2s, "_query_and_cache_server", AsyncMock(side_effect=AssertionError("UDP"))
    )
    store.client.mget.return_value = [[None, "oops", "[]", "{}"][i % 4] for i in range(count)]
    assert await disk.get_many_disk_space(servers, cache_only=True) == [None] * count
    assert await a2s.get_many_cached_info(servers) == [None] * count
    store.client.mget.return_value = [json.dumps(json.dumps({"success": True}))] * count
    assert await a2s.get_many_cached_info(servers) == [{"success": True}] * count
    store.client.mget.side_effect = OSError("offline")
    assert await disk.get_many_disk_space(servers, cache_only=True) == [None] * count
    assert await a2s.get_many_cached_info(servers) == [None] * count


@pytest.mark.asyncio
@pytest.mark.parametrize("all_servers", [False, True])
async def test_snapshot_releases_read_transaction_before_any_remote_work(monkeypatch, all_servers):
    db = SimpleNamespace(commit=AsyncMock())
    rows = [server(4)]
    mine = AsyncMock(return_value=rows)
    fleet = AsyncMock(return_value=rows)
    monkeypatch.setattr(Server, "get_all_by_user", mine)
    monkeypatch.setattr(Server, "get_all", fleet)
    result = await load_telemetry_servers(db, 7, all_servers=all_servers)
    assert result == rows
    db.commit.assert_awaited_once_with()
    if all_servers:
        fleet.assert_awaited_once_with(db, skip=0, limit=1000)
        mine.assert_not_called()
    else:
        mine.assert_awaited_once_with(db, 7, skip=0, limit=1000)
        fleet.assert_not_called()
    db.commit.side_effect = RuntimeError("release failed")
    with pytest.raises(RuntimeError, match="release failed"):
        await load_telemetry_servers(db, 7)


@pytest.mark.asyncio
async def test_ssh_limits_are_shared_across_domains_requests_and_same_host(store, monkeypatch):
    disk, host, steam = DiskSpaceService(), HostSystemInfoService(), SteamInfService()
    active = peak = 0
    active_hosts = set()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def probe(item):
        nonlocal active, peak
        key = (item.host.casefold(), item.ssh_port)
        assert key not in active_hosts
        active_hosts.add(key)
        active += 1
        peak = max(active, peak)
        if active == 4:
            entered.set()
        try:
            await release.wait()
        finally:
            active -= 1
            active_hosts.remove(key)

    async def disk_probe(item):
        await probe(item)
        return True, {"used_gb": item.id}

    async def host_probe(item):
        await probe(item)
        return parse_host_system_info("PROBE_OK=1", item.id)

    async def steam_probe(item, **_kwargs):
        await probe(item)
        return True, "1.2.3.4", "99"

    monkeypatch.setattr(disk, "_read_disk_space_unlimited", disk_probe)
    monkeypatch.setattr(host, "_collect", host_probe)
    monkeypatch.setattr(steam, "_refresh_and_cache", steam_probe)
    items = [server(i, f"host-{i % 6}") for i in range(24)]
    pending = asyncio.create_task(
        telemetry_runtime.collect_ordered(
            [
                disk.get_many_disk_space(items[:8], force_refresh=True),
                host.get_many_host_system_info(items[8:16], force_refresh=True),
                *[
                    steam.get_version_from_steam_inf(item, force_refresh=True)
                    for item in items[16:]
                ],
            ]
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    assert peak == 4
    release.set()
    result = await pending
    assert len(result[0]) == len(result[1]) == 8
    assert active == 0 and not active_hosts
    assert telemetry_runtime.ssh_probe_limiter.active_key_count == 0
    store.client.mget.assert_not_called()


@pytest.mark.asyncio
async def test_a2s_foreground_background_and_single_refresh_share_limits(store, monkeypatch):
    service = A2SCacheService()
    active_ids = set()
    entered = asyncio.Event()
    release = asyncio.Event()
    peak = 0

    async def probe(item):
        nonlocal peak
        assert item.id not in active_ids
        active_ids.add(item.id)
        peak = max(peak, len(active_ids))
        if len(active_ids) == 8:
            entered.set()
        try:
            await release.wait()
        finally:
            active_ids.remove(item.id)

    monkeypatch.setattr(service, "_query_and_cache_server", probe)
    items = [server(i) for i in range(12)]
    store.client.mget.return_value = [None] * len(items)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items))

    monkeypatch.setattr("modules.database.async_session_maker", Session)
    task = asyncio.create_task(
        telemetry_runtime.collect_ordered(
            [
                service.get_many_cached_info(items, force_refresh=True),
                service._query_all_servers(),
                service.refresh_cached_info(items[0]),
            ]
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    release.set()
    await task
    assert peak == 8
    assert not active_ids
    assert service._probe_limiter.active_key_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["disk", "host", "steam"])
async def test_cancellation_disconnects_ssh_and_releases_slots_and_host_lock(
    kind, store, monkeypatch
):
    entered = asyncio.Event()
    disconnected = []

    class SSH:
        async def connect(self, item):
            self.identifier = item.id
            return True, "ok"

        async def execute_command(self, *_args, **_kwargs):
            entered.set()
            await asyncio.Event().wait()

        async def disconnect(self):
            disconnected.append(self.identifier)

    for module in MODULES[:2]:
        monkeypatch.setattr(module, "SSHManager", SSH)
    monkeypatch.setattr(MODULES[3], "_ssh_manager_factory", SSH)
    items = [server(i, "same-host") for i in range(20)]
    if kind == "disk":
        job = DiskSpaceService().get_many_disk_space(items, force_refresh=True)
    elif kind == "host":
        job = HostSystemInfoService().get_many_host_system_info(items, force_refresh=True)
    else:
        service = SteamInfService()
        job = telemetry_runtime.collect_ordered(
            service.get_steam_inf_details(item, True) for item in items
        )
    task = asyncio.create_task(job)
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(disconnected) == 1
    assert telemetry_runtime.ssh_probe_limiter.active_key_count == 0
    if kind == "host":
        store.client.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_child_failure_drains_siblings_and_keeps_original_exception():
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def wait():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def fail():
        await started.wait()
        raise ValueError("probe failed")

    with pytest.raises(ValueError, match="probe failed"):
        await telemetry_runtime.collect_ordered([wait(), fail()])
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_host_cache_miss_rechecks_under_lock_and_preserves_failure_ttl(store, monkeypatch):
    service = HostSystemInfoService()
    store.client.mget.return_value = [json.dumps({"server_id": 999}), None]
    monkeypatch.setattr(
        service,
        "_collect",
        AsyncMock(
            side_effect=[
                parse_host_system_info("PROBE_OK=1", 1),
                parse_host_system_info("", 2),
            ]
        ),
    )
    results = await service.get_many_host_system_info([server(1), server(2)])
    assert [item["success"] for item in results] == [True, False]
    writes = {
        call.args[0]: call.kwargs["ex"]
        for call in store.client.set.call_args_list
        if "nx" not in call.kwargs
    }
    assert writes == {
        "telemetry-test:host_system_info:1": 900,
        "telemetry-test:host_system_info:2": 60,
    }
    assert store.client.eval.await_count == 2


@pytest.mark.asyncio
async def test_steam_refresh_cache_timeout_is_bounded(store, monkeypatch):
    service = SteamInfService()
    monkeypatch.setattr(service, "_refresh_and_cache", AsyncMock(side_effect=TimeoutError))
    assert await service.get_steam_inf_details(server(1), True) == (False, None, None)
    assert telemetry_runtime.ssh_probe_limiter.active_key_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 20, 100, 1000])
@pytest.mark.parametrize("scope", ["mine", "all"])
@pytest.mark.parametrize("kind", ["disk", "host", "a2s"])
async def test_overview_probes_only_authorized_snapshots_after_commit(
    count, scope, kind, monkeypatch
):
    from fastapi import HTTPException

    from api.routes.v1 import overview

    transaction_open = True
    rows = [server(i) for i in reversed(range(count))]
    mine, fleet = AsyncMock(return_value=rows), AsyncMock(return_value=rows)
    monkeypatch.setattr(Server, "get_all_by_user", mine)
    monkeypatch.setattr(Server, "get_all", fleet)

    async def commit():
        nonlocal transaction_open
        transaction_open = False

    async def probe(items, **kwargs):
        assert not transaction_open
        assert items == rows
        assert kwargs["force_refresh"] is True
        return [parse_host_system_info("", item.id) if kind == "host" else None for item in items]

    route, service, method = {
        "disk": (
            overview.read_overview_disk_space,
            overview.disk_space_service,
            "get_many_disk_space",
        ),
        "host": (
            overview.read_overview_host_system_info,
            overview.host_system_info_service,
            "get_many_host_system_info",
        ),
        "a2s": (
            overview.read_overview_a2s_cache,
            overview.a2s_cache_service,
            "get_many_cached_info",
        ),
    }[kind]
    mock = AsyncMock(side_effect=probe)
    monkeypatch.setattr(service, method, mock)
    db = SimpleNamespace(commit=commit)
    user = SimpleNamespace(id=7, is_admin=scope == "all")
    result = await route(db, user, scope=scope, force_refresh=True)
    assert [item.server_id for item in result.servers] == [item.id for item in rows]
    if scope == "all":
        fleet.assert_awaited_once_with(db, skip=0, limit=1000)
        mine.assert_not_called()
    else:
        mine.assert_awaited_once_with(db, 7, skip=0, limit=1000)
        fleet.assert_not_called()
    mock.reset_mock()
    user.is_admin = False
    with pytest.raises(HTTPException) as caught:
        await route(db, user, scope="all", force_refresh=True)
    assert caught.value.status_code == 403
    mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["disk", "host", "a2s"])
async def test_controlled_cache_latency_comparison(kind, store):
    from time import perf_counter

    items = [server(i) for i in range(20)]
    payload = {
        "disk": {"used_gb": 1},
        "host": parse_host_system_info("PROBE_OK=1", 0),
        "a2s": {"success": True},
    }[kind]

    def encoded(key):
        value = {**payload, "server_id": int(key.rsplit(":", 1)[1])}
        return json.dumps(value)

    async def get(key):
        await asyncio.sleep(0.01)
        return encoded(key)

    async def mget(keys):
        await asyncio.sleep(0.01)
        return [encoded(key) for key in keys]

    store.client.get.side_effect = get
    store.client.mget.side_effect = mget
    disk, host, a2s = DiskSpaceService(), HostSystemInfoService(), A2SCacheService()

    async def single(item):
        if kind == "disk":
            return (await disk.get_disk_space(item, cache_only=True))[1]
        if kind == "host":
            return await host.get_host_system_info(item)
        return await a2s.get_cached_info(item.id)

    method = {
        "disk": disk.get_many_disk_space,
        "host": host.get_many_host_system_info,
        "a2s": a2s.get_many_cached_info,
    }[kind]
    started = perf_counter()
    before = [await single(item) for item in items]
    serial_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    after = await method(items)
    batch_ms = (perf_counter() - started) * 1000
    assert after == before
    assert store.client.get.await_count == 20
    assert store.client.mget.await_count == 1
    print(f"BENCH cache {kind}: serial={serial_ms:.1f}ms batch={batch_ms:.1f}ms calls=20->1")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,limit", [("disk", 4), ("host", 4), ("a2s", 8), ("steam", 4)])
async def test_controlled_probe_latency_comparison(kind, limit, store, monkeypatch):
    from time import perf_counter

    active = peak = calls = 0
    items = [server(i) for i in range(20)]
    disk, host, a2s, steam = (
        DiskSpaceService(),
        HostSystemInfoService(),
        A2SCacheService(),
        SteamInfService(),
    )

    async def probe(item, **_kwargs):
        nonlocal active, peak, calls
        active += 1
        peak = max(peak, active)
        calls += 1
        try:
            await asyncio.sleep(0.01)
            if kind == "disk":
                return True, {"used_gb": item.id}
            if kind == "host":
                return parse_host_system_info("PROBE_OK=1", item.id)
            if kind == "steam":
                return True, "1.2.3.4", "99"
            return None
        finally:
            active -= 1

    monkeypatch.setattr(disk, "_read_disk_space_unlimited", probe)
    monkeypatch.setattr(host, "_collect", probe)
    monkeypatch.setattr(a2s, "_query_and_cache_server", probe)
    monkeypatch.setattr(steam, "_refresh_and_cache", probe)
    store.client.mget.return_value = [None] * len(items)

    async def single(item):
        if kind == "disk":
            return (await disk.get_disk_space(item, force_refresh=True))[1]
        if kind == "host":
            return await host.get_host_system_info(item, force_refresh=True)
        if kind == "steam":
            return await steam.get_steam_inf_details(item, True)
        return await a2s.refresh_cached_info(item)

    started = perf_counter()
    before = [await single(item) for item in items]
    serial_ms = (perf_counter() - started) * 1000
    assert calls == 20 and peak == 1
    calls = peak = 0
    started = perf_counter()
    if kind == "steam":
        after = await telemetry_runtime.collect_ordered(single(item) for item in items)
    else:
        method = {
            "disk": disk.get_many_disk_space,
            "host": host.get_many_host_system_info,
            "a2s": a2s.get_many_cached_info,
        }[kind]
        after = await method(items, force_refresh=True)
    batch_ms = (perf_counter() - started) * 1000
    assert before == after
    assert calls == 20 and peak == limit and active == 0
    print(
        f"BENCH probe {kind}: serial={serial_ms:.1f}ms batch={batch_ms:.1f}ms peak=1->{peak} calls=20->20"
    )


@pytest.mark.asyncio
async def test_a2s_empty_cache_preserves_single_read_and_presenter_semantics(store):
    from api.routes.v1.overview import _a2s_view

    service = A2SCacheService()
    # A plain empty dict was a miss; the historical extra JSON layer was
    # decoded after the truthiness check and must retain that distinction.
    values = [json.dumps({}), json.dumps("{}")]
    store.client.mget.return_value = values
    store.client.get.side_effect = values
    batch = await service.get_many_cached_info([server(1), server(2)])
    assert batch == [None, {}]
    assert batch == [await service.get_cached_info(i) for i in [1, 2]]
    assert [_a2s_view(i, value).cached for i, value in enumerate(batch)] == [False, True]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 20, 100, 1000])
async def test_host_batch_misses_and_redis_outage_keep_result_order(count, store, monkeypatch):
    service = HostSystemInfoService()
    items = [server(i) for i in reversed(range(count))]
    collect = AsyncMock(side_effect=lambda item: parse_host_system_info("PROBE_OK=1", item.id))
    monkeypatch.setattr(service, "_collect", collect)
    store.client.mget.return_value = [None] * count
    before = await service.get_many_host_system_info(items)
    assert [item["server_id"] for item in before] == [item.id for item in items]
    assert all(not item["cached"] and item["success"] for item in before)
    assert collect.await_count == count
    # Model a full Redis outage, including the existing best-effort lock path.
    store.client.mget.side_effect = OSError("offline")
    store.client.get.side_effect = OSError("offline")
    store.client.set.side_effect = OSError("offline")
    after = await service.get_many_host_system_info(items)
    assert after == before
    assert collect.await_count == 2 * count
    assert telemetry_runtime.ssh_probe_limiter.active_key_count == 0
