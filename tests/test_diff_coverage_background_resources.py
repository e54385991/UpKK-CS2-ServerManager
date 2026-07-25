"""Diff-coverage tests for application-owned background resources."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api import lifecycle as lifecycle_module
from api.lifecycle import ApplicationLifecycle
from modules.models import (
    AuthType,
    DeploymentLog,
    ManagedPlugin,
    ScheduledTask,
    Server,
    User,
)
from services.auto_update_service import AutoUpdateService
from services.plugin_auto_update_service import (
    PluginAutoUpdateService,
    record_framework_installation,
    record_known_github_installation,
)
from services.scheduled_task_service import ScheduledTaskService
from services.steam_inf_service import SteamInfService


class _Rows:
    def __init__(self, values=()):
        self.values = list(values)

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class _Session:
    def __init__(self, *, values=(), objects=None, state=None):
        self.values = list(values)
        self.objects = objects or {}
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _statement):
        return _Rows(self.values)

    async def get(self, model, object_id):
        if self.state is not None and model is DeploymentLog:
            return self.state.log
        return self.objects.get((model, object_id), self.objects.get(model))

    async def commit(self):
        return None

    async def refresh(self, _value):
        return None

    def add(self, value):
        if self.state is not None and isinstance(value, DeploymentLog):
            value.id = 1
            self.state.log = value


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def acquire(self):
        return None


def _server(server_id: int = 31) -> Server:
    return Server(
        id=server_id,
        user_id=7,
        name=f"server-{server_id}",
        host="server.example.com",
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
    )


def _user() -> User:
    return User(
        id=7,
        username="owner",
        email="owner@example.com",
        hashed_password="hash",
    )


@pytest.mark.asyncio
async def test_lifecycle_ssh_factory_fails_when_pool_is_unavailable(monkeypatch) -> None:
    from services.a2s_cache_service import A2S_CACHE_SERVICE_KEY
    from services.auto_update_service import auto_update_service
    from services.maintenance_lock import MAINTENANCE_LOCK_SERVICE_KEY
    from services.plugin_auto_update_service import plugin_auto_update_service
    from services.s3_backup_service import S3_BACKUP_SERVICE_KEY
    from services.scheduled_task_service import scheduled_task_service
    from services.server_monitor import server_monitor
    from services.ssh_health_monitor import ssh_health_monitor
    from services.steam_inf_service import steam_inf_service

    class _DatabaseContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    scheduled_start = AsyncMock()
    for service in (
        auto_update_service,
        plugin_auto_update_service,
        steam_inf_service,
        ssh_health_monitor,
    ):
        monkeypatch.setattr(service, "start", AsyncMock())
        monkeypatch.setattr(service, "stop", AsyncMock())
    monkeypatch.setattr(scheduled_task_service, "start", scheduled_start)
    monkeypatch.setattr(scheduled_task_service, "stop", AsyncMock())
    monkeypatch.setattr(server_monitor, "stop_all", AsyncMock())
    monkeypatch.setattr(
        lifecycle_module.Server,
        "get_all_with_panel_monitoring",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(lifecycle_module, "require_database_current", AsyncMock())
    monkeypatch.setattr(lifecycle_module, "_cleanup_runtime_tasks", AsyncMock())

    container = SimpleNamespace(
        database=SimpleNamespace(
            engine=object(),
            session_factory=lambda: _DatabaseContext(),
            close=AsyncMock(),
        ),
        redis=SimpleNamespace(
            delete_by_pattern=AsyncMock(return_value=0),
            close=AsyncMock(),
        ),
        http=SimpleNamespace(close=AsyncMock()),
        ssh_pool=None,
        task_supervisor=SimpleNamespace(start=Mock(), shutdown=AsyncMock()),
        legacy_runtime=True,
        services={
            A2S_CACHE_SERVICE_KEY: SimpleNamespace(start=AsyncMock(), stop=AsyncMock()),
            MAINTENANCE_LOCK_SERVICE_KEY: SimpleNamespace(),
            S3_BACKUP_SERVICE_KEY: SimpleNamespace(close=AsyncMock()),
        },
    )
    lifecycle = ApplicationLifecycle(container=container)  # type: ignore[arg-type]

    await lifecycle.start()

    manager_factory = scheduled_start.await_args.kwargs["ssh_manager_factory"]
    with pytest.raises(RuntimeError, match="pool is unavailable"):
        manager_factory()

    await lifecycle.stop()


@pytest.mark.asyncio
async def test_auto_update_runtime_resources_start_and_loop(monkeypatch) -> None:
    owned_steam = SimpleNamespace()

    def owned_factory():
        return SimpleNamespace()

    owned_lock = SimpleNamespace()
    override_lock = SimpleNamespace()
    service = AutoUpdateService(
        steam_service=owned_steam,  # type: ignore[arg-type]
        ssh_manager_factory=owned_factory,  # type: ignore[arg-type]
        lock_service=owned_lock,  # type: ignore[arg-type]
    )

    assert service._configured_ssh_manager_factory(owned_factory) is owned_factory
    assert service._runtime_lock_service(override_lock) is override_lock
    assert service._runtime_lock_service(None) is owned_lock

    loop = AsyncMock()
    monkeypatch.setattr(service, "_update_loop", loop)
    await service.start(
        steam_service=owned_steam,  # type: ignore[arg-type]
        ssh_manager_factory=owned_factory,  # type: ignore[arg-type]
        lock_service=override_lock,  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)
    loop.assert_awaited_once_with(owned_steam, owned_factory, override_lock)
    await service.stop()

    loop_service = AutoUpdateService()
    loop_service.running = True

    async def check_once(**kwargs):
        assert kwargs == {
            "steam_service": owned_steam,
            "ssh_manager_factory": owned_factory,
            "lock_service": owned_lock,
        }
        loop_service.running = False

    sleep = AsyncMock()
    monkeypatch.setattr(loop_service, "_check_and_update_servers", check_once)
    monkeypatch.setattr("services.auto_update_service.asyncio.sleep", sleep)
    await loop_service._update_loop(
        owned_steam,  # type: ignore[arg-type]
        owned_factory,  # type: ignore[arg-type]
        owned_lock,  # type: ignore[arg-type]
    )
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_update_check_and_version_read_resource_branches(monkeypatch) -> None:
    server = _server()
    server.update_check_interval_hours = 2
    server.last_update_check = None
    server.current_game_version = None

    steam = SimpleNamespace(
        should_check_version=Mock(return_value=True),
        check_version=AsyncMock(return_value=(True, {"up_to_date": True})),
    )
    checked = AsyncMock()
    service = AutoUpdateService(steam_service=steam)  # type: ignore[arg-type]
    monkeypatch.setattr(
        Server,
        "get_all_with_auto_update",
        AsyncMock(return_value=[server]),
    )
    monkeypatch.setattr(
        "modules.database.async_session_maker",
        lambda: _Session(),
    )
    monkeypatch.setattr(service, "_check_and_update_server", checked)

    await service._check_and_update_servers(steam_service=steam)  # type: ignore[arg-type]

    checked.assert_awaited_once()
    assert checked.await_args.kwargs["steam_service"] is steam

    async def read_without_new_keyword(received_server):
        assert received_server is server
        return True, "1.42.3.4"

    monkeypatch.setattr(
        "services.auto_update_service.steam_inf_service.get_version_from_steam_inf",
        read_without_new_keyword,
    )
    read_service = AutoUpdateService(steam_service=steam)  # type: ignore[arg-type]
    await read_service._check_and_update_server(server, steam_service=steam)  # type: ignore[arg-type]
    steam.check_version.assert_awaited_once_with("1.42.3.4")


@pytest.mark.asyncio
async def test_auto_update_explicit_verification_and_trigger(monkeypatch) -> None:
    server = _server(44)

    def manager_factory():
        return SimpleNamespace(
            update_server=AsyncMock(return_value=(True, "updated")),
        )

    refresh = AsyncMock(return_value=(True, "1.42.3.4"))
    monkeypatch.setattr(
        "services.auto_update_service.steam_inf_service.refresh_version_cache",
        refresh,
    )
    service = AutoUpdateService(ssh_manager_factory=manager_factory)  # type: ignore[arg-type]
    service.VERSION_VERIFICATION_TIMEOUT_SECONDS = 1
    service.VERSION_VERIFICATION_POLL_INTERVAL_SECONDS = 0

    verified = await service._wait_for_updated_version(
        server,
        required_version="1.42.3.4",
        log_progress=AsyncMock(),
    )

    assert verified == (True, "1.42.3.4", None)
    assert refresh.await_args.kwargs["ssh_manager_factory"] is manager_factory

    state = SimpleNamespace(log=None)
    monkeypatch.setattr(
        "modules.database.async_session_maker",
        lambda: _Session(state=state),
    )
    monkeypatch.setattr(
        "services.auto_update_service.discord_notification_service.queue_notify",
        Mock(),
    )
    wait_for_version = AsyncMock(return_value=(True, "1.42.3.4", None))
    monkeypatch.setattr(service, "_wait_for_updated_version", wait_for_version)
    lock_service = SimpleNamespace(
        get=Mock(return_value=_Lock()),
        is_locked=AsyncMock(return_value=False),
    )

    await service._trigger_server_update(
        server,
        current_version="1.42.3.3",
        required_version="1.42.3.4",
        version_source="steam.inf",
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
        lock_service=lock_service,  # type: ignore[arg-type]
    )

    assert state.log.status == "success"
    assert wait_for_version.await_args.kwargs["ssh_manager_factory"] is manager_factory


@pytest.mark.asyncio
async def test_plugin_runtime_start_loop_and_server_sweep(monkeypatch) -> None:
    constructor_http = SimpleNamespace(name="constructor")
    runtime_http = SimpleNamespace(name="runtime")

    def constructor_factory():
        return SimpleNamespace()

    def runtime_factory():
        return SimpleNamespace()

    constructor_lock = SimpleNamespace()
    runtime_lock = SimpleNamespace()
    service = PluginAutoUpdateService(
        http_resource=constructor_http,
        ssh_manager_factory=constructor_factory,  # type: ignore[arg-type]
        lock_service=constructor_lock,  # type: ignore[arg-type]
    )
    assert service.http_resource is constructor_http
    assert service._runtime_lock(runtime_lock) is runtime_lock
    assert service._runtime_lock(None) is constructor_lock

    loop = AsyncMock()
    monkeypatch.setattr(service, "_loop", loop)
    await service.start(
        http_resource=runtime_http,
        ssh_manager_factory=runtime_factory,  # type: ignore[arg-type]
        lock_service=runtime_lock,  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)
    loop.assert_awaited_once_with(
        http_resource=runtime_http,
        ssh_manager_factory=runtime_factory,
        lock_service=runtime_lock,
    )
    await service.stop()

    loop_service = PluginAutoUpdateService()
    loop_service.running = True

    async def check_once(**kwargs):
        assert kwargs["http_resource"] is runtime_http
        loop_service.running = False

    sleep = AsyncMock()
    monkeypatch.setattr(loop_service, "check_all_servers", check_once)
    monkeypatch.setattr("services.plugin_auto_update_service.asyncio.sleep", sleep)
    await loop_service._loop(
        http_resource=runtime_http,
        ssh_manager_factory=runtime_factory,  # type: ignore[arg-type]
        lock_service=runtime_lock,  # type: ignore[arg-type]
    )
    sleep.assert_awaited_once()

    server = _server(55)
    server.enable_plugin_auto_update = True
    server.last_plugin_update_check = None
    server.plugin_update_check_interval_hours = 1
    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker",
        lambda: _Session(values=[server]),
    )
    sweep_service = PluginAutoUpdateService()
    checked = AsyncMock()
    monkeypatch.setattr(sweep_service, "check_server", checked)
    await sweep_service.check_all_servers(
        http_resource=runtime_http,
        ssh_manager_factory=runtime_factory,  # type: ignore[arg-type]
        lock_service=runtime_lock,  # type: ignore[arg-type]
    )
    assert checked.await_args.kwargs["http_resource"] is runtime_http
    assert checked.await_args.kwargs["ssh_manager_factory"] is runtime_factory
    assert checked.await_args.kwargs["lock_service"] is runtime_lock


@pytest.mark.asyncio
async def test_plugin_metamod_and_install_resource_branches(monkeypatch) -> None:
    server = _server(66)
    user = _user()
    manager = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "connected")),
        _fetch_latest_metamod_url=AsyncMock(
            return_value=(True, "https://example.com/mmsource.tar.gz")
        ),
        disconnect=AsyncMock(),
        update_metamod=AsyncMock(return_value=(True, "metamod")),
        update_counterstrikesharp=AsyncMock(return_value=(True, "css")),
    )

    def manager_factory():
        return manager

    service = PluginAutoUpdateService(ssh_manager_factory=manager_factory)  # type: ignore[arg-type]
    ok, latest, error = await service._latest_metamod(server)
    assert (ok, latest["version"], error) == (True, "mmsource.tar.gz", "")

    release = {
        "release_id": "2",
        "version": "v2",
        "asset": {
            "name": "plugin.zip",
            "browser_download_url": (
                "https://github.com/owner/repo/releases/download/v2/plugin.zip"
            ),
        },
    }
    metamod = ManagedPlugin(
        server_id=server.id,
        source_type="framework",
        source_key="metamod",
        display_name="Metamod",
        framework_key="metamod",
    )
    css = ManagedPlugin(
        server_id=server.id,
        source_type="framework",
        source_key="counterstrikesharp",
        display_name="CounterStrikeSharp",
        framework_key="counterstrikesharp",
    )
    assert await service._install_item(server, user, metamod, release) == (True, "metamod")
    assert await service._install_item(server, user, css, release) == (True, "css")

    ordinary = ManagedPlugin(
        server_id=server.id,
        source_type="github",
        source_key="owner/repo",
        display_name="Plugin",
        repo_url="https://github.com/owner/repo",
    )
    outbound_http = SimpleNamespace(name="owned")
    install = AsyncMock(return_value=SimpleNamespace(success=True, message="installed"))
    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker",
        lambda: _Session(),
    )
    monkeypatch.setattr(
        "services.plugin_auto_update_service.install_github_plugin",
        install,
    )

    result = await service._install_item(
        server,
        user,
        ordinary,
        release,
        http_resource=outbound_http,
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
    )
    assert result == (True, "installed")
    assert install.await_args.kwargs["http_resource"] is outbound_http


def _plugin_pipeline_session(server, user, items):
    objects = {
        Server: server,
        User: user,
        **{(ManagedPlugin, item.id): item for item in items},
    }
    return _Session(values=items, objects=objects)


@pytest.mark.asyncio
async def test_plugin_pipeline_explicit_and_legacy_release_branches(monkeypatch) -> None:
    server = _server(77)
    server.enable_plugin_auto_update = True
    user = _user()
    metamod = ManagedPlugin(
        id=1,
        server_id=server.id,
        source_type="framework",
        source_key="metamod",
        display_name="Metamod",
        framework_key="metamod",
        installed_release_id="old",
        installed_version="old",
        auto_update_enabled=True,
    )
    github = ManagedPlugin(
        id=2,
        server_id=server.id,
        source_type="github",
        source_key="owner/repo",
        display_name="Plugin",
        repo_url="https://github.com/owner/repo",
        installed_release_id="old",
        installed_version="old",
        auto_update_enabled=True,
    )
    latest = {
        "release_id": "new",
        "version": "new",
        "asset": {
            "name": "plugin.zip",
            "browser_download_url": "https://example.com/plugin.zip",
        },
    }
    lock_service = SimpleNamespace(get=Mock(return_value=_Lock()))
    service = PluginAutoUpdateService(lock_service=lock_service)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_publish_status", AsyncMock())
    latest_metamod = AsyncMock(return_value=(True, latest, ""))
    latest_github = AsyncMock(return_value=(True, latest, ""))
    install = AsyncMock(return_value=(True, "installed"))
    monkeypatch.setattr(service, "_latest_metamod", latest_metamod)
    monkeypatch.setattr(service, "_latest_github_release", latest_github)
    monkeypatch.setattr(service, "_install_item", install)
    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker",
        lambda: _plugin_pipeline_session(server, user, [metamod, github]),
    )
    monkeypatch.setattr(
        "services.plugin_auto_update_service.discord_notification_service.queue_notify",
        Mock(),
    )
    outbound_http = SimpleNamespace(name="owned")

    def manager_factory():
        return SimpleNamespace()

    result = await service.check_server(
        server.id,
        http_resource=outbound_http,
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
        lock_service=lock_service,  # type: ignore[arg-type]
    )
    assert result["success"] is True
    assert latest_metamod.await_args.kwargs["ssh_manager_factory"] is manager_factory
    assert latest_github.await_args.kwargs["http_resource"] is outbound_http
    assert install.await_args_list[0].kwargs["ssh_manager_factory"] is manager_factory

    metamod.installed_release_id = "new"
    metamod.installed_version = "new"
    legacy_service = PluginAutoUpdateService(
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
        lock_service=lock_service,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(legacy_service, "_publish_status", AsyncMock())
    legacy_latest = AsyncMock(return_value=(True, latest, ""))
    monkeypatch.setattr(legacy_service, "_latest_metamod", legacy_latest)
    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker",
        lambda: _plugin_pipeline_session(server, user, [metamod]),
    )

    legacy_result = await legacy_service.check_server(server.id)
    assert legacy_result["success"] is True
    legacy_latest.assert_awaited_once_with(server)


@pytest.mark.asyncio
async def test_plugin_record_helpers_forward_resources(monkeypatch) -> None:
    from services import plugin_auto_update_service as plugin_module

    server = _server(88)
    user = _user()
    latest = {
        "release_id": "2",
        "version": "v2",
        "asset": {"name": "plugin.zip", "browser_download_url": "https://example.com"},
    }
    metamod_latest = AsyncMock(return_value=(True, latest, ""))
    github_latest = AsyncMock(return_value=(True, latest, ""))
    upsert = AsyncMock()
    monkeypatch.setattr(
        plugin_module.plugin_auto_update_service,
        "_latest_metamod",
        metamod_latest,
    )
    monkeypatch.setattr(
        plugin_module.plugin_auto_update_service,
        "_latest_github_release",
        github_latest,
    )
    monkeypatch.setattr(plugin_module, "upsert_managed_plugin", upsert)
    outbound_http = SimpleNamespace(name="owned")

    def manager_factory():
        return SimpleNamespace()

    await record_framework_installation(
        server,
        user,
        "metamod",
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
    )
    await record_framework_installation(
        server,
        user,
        "counterstrikesharp",
        http_resource=outbound_http,
    )
    await record_known_github_installation(
        server,
        user,
        "https://github.com/owner/repo",
        "Plugin",
        "*.zip",
        http_resource=outbound_http,
    )

    assert metamod_latest.await_args.kwargs["ssh_manager_factory"] is manager_factory
    assert github_latest.await_args_list[0].kwargs["http_resource"] is outbound_http
    assert github_latest.await_args_list[1].kwargs["http_resource"] is outbound_http
    assert upsert.await_count == 3


@pytest.mark.asyncio
async def test_scheduled_service_start_defaults(monkeypatch) -> None:
    http = SimpleNamespace(name="owned")

    def manager_factory():
        return SimpleNamespace()

    lock_service = SimpleNamespace()
    s3_service = SimpleNamespace()
    service = ScheduledTaskService(
        session_factory=lambda: _Session(),  # type: ignore[arg-type]
        http_resource=http,
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
        lock_service=lock_service,  # type: ignore[arg-type]
        s3_service=s3_service,  # type: ignore[arg-type]
    )
    loop = AsyncMock()
    monkeypatch.setattr(service, "_execution_loop", loop)
    monkeypatch.setattr(service, "_calculate_all_next_runs", AsyncMock())
    await service.start()
    await asyncio.sleep(0)
    loop.assert_awaited_once_with(
        http_resource=http,
        ssh_manager_factory=manager_factory,
        lock_service=lock_service,
        s3_service=s3_service,
    )
    await service.stop()


@pytest.mark.asyncio
async def test_scheduled_execution_loop_propagates_resources(monkeypatch) -> None:
    http = SimpleNamespace(name="owned")
    lock_service = SimpleNamespace()
    s3_service = SimpleNamespace()

    def manager_factory():
        return SimpleNamespace()

    loop_service = ScheduledTaskService()
    loop_service.running = True

    async def check_once(**kwargs):
        assert kwargs["s3_service"] is loop_service._s3_service
        loop_service.running = False

    monkeypatch.setattr(loop_service, "_check_and_execute_tasks", check_once)

    async def stop_sleep(_delay):
        raise RuntimeError("stop loop")

    monkeypatch.setattr("services.scheduled_task_service.asyncio.sleep", stop_sleep)
    with pytest.raises(RuntimeError, match="stop loop"):
        await loop_service._execution_loop(
            http_resource=http,
            ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
            lock_service=lock_service,  # type: ignore[arg-type]
            s3_service=s3_service,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_scheduled_dispatch_uses_constructor_resources(monkeypatch) -> None:
    http = SimpleNamespace(name="owned")
    lock_service = SimpleNamespace()
    s3_service = SimpleNamespace()

    def manager_factory():
        return SimpleNamespace()

    task = ScheduledTask(
        id=9,
        server_id=31,
        name="nightly",
        action="restart",
        enabled=True,
        schedule_type="daily",
        schedule_value="04:00",
    )
    dispatch_service = ScheduledTaskService(
        session_factory=lambda: _Session(values=[task]),  # type: ignore[arg-type]
        http_resource=http,
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
        lock_service=lock_service,  # type: ignore[arg-type]
        s3_service=s3_service,  # type: ignore[arg-type]
    )
    execute = AsyncMock()
    monkeypatch.setattr(dispatch_service, "_execute_task", execute)
    await dispatch_service._check_and_execute_tasks()
    await asyncio.gather(*dispatch_service.running_tasks.values())
    execute.assert_awaited_once()
    assert execute.await_args.kwargs["s3_service"] is s3_service
    await dispatch_service.stop()


@pytest.mark.asyncio
async def test_steam_inf_factory_branches_and_periodic_propagation(monkeypatch) -> None:
    def manager_factory():
        return SimpleNamespace()

    no_factory = SteamInfService()
    no_factory_loop = AsyncMock()
    monkeypatch.setattr(no_factory, "_refresh_loop", no_factory_loop)
    await no_factory.start()
    await asyncio.sleep(0)
    no_factory_loop.assert_awaited_once_with()
    await no_factory.stop()

    explicit = SteamInfService()
    explicit_loop = AsyncMock()
    monkeypatch.setattr(explicit, "_refresh_loop", explicit_loop)
    await explicit.start(ssh_manager_factory=manager_factory)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    assert explicit_loop.await_args.kwargs["ssh_manager_factory"] is manager_factory
    await explicit.stop()

    sleep = AsyncMock()
    monkeypatch.setattr("services.steam_inf_service.asyncio.sleep", sleep)
    legacy_loop = SteamInfService()
    legacy_loop.running = True

    async def legacy_refresh():
        legacy_loop.running = False

    monkeypatch.setattr(legacy_loop, "_periodic_refresh_all", legacy_refresh)
    await legacy_loop._refresh_loop()

    owned_loop = SteamInfService()
    owned_loop.running = True

    async def owned_refresh(*, ssh_manager_factory):
        assert ssh_manager_factory is manager_factory
        owned_loop.running = False

    monkeypatch.setattr(owned_loop, "_periodic_refresh_all", owned_refresh)
    await owned_loop._refresh_loop(ssh_manager_factory=manager_factory)  # type: ignore[arg-type]
    assert sleep.await_count == 2

    server = _server(99)
    periodic = SteamInfService()
    refresh_one = AsyncMock()
    monkeypatch.setattr(periodic, "_refresh_server_with_timeout", refresh_one)
    monkeypatch.setattr(
        "modules.database.async_session_maker",
        lambda: _Session(values=[server]),
    )
    await periodic._periodic_refresh_all(
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
    )
    assert refresh_one.await_args.kwargs["ssh_manager_factory"] is manager_factory

    refresh_service = SteamInfService()
    read = AsyncMock(return_value=(True, "1.42.3.4"))
    monkeypatch.setattr(refresh_service, "get_version_from_steam_inf", read)
    await refresh_service._refresh_server_with_timeout(
        server,
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
    )
    assert read.await_args.kwargs["ssh_manager_factory"] is manager_factory


@pytest.mark.asyncio
async def test_steam_inf_read_and_refresh_legacy_and_explicit_branches(monkeypatch) -> None:
    server = _server(111)
    service = SteamInfService()
    read_file = AsyncMock(return_value=(False, None))
    monkeypatch.setattr(service, "_read_version_from_file", read_file)
    assert await service.get_version_from_steam_inf(server, force_refresh=True) == (False, None)
    read_file.assert_awaited_once_with(server)

    get_version = AsyncMock(return_value=(False, None))
    monkeypatch.setattr(service, "get_version_from_steam_inf", get_version)
    assert await service.refresh_version_cache(server) == (False, None)
    get_version.assert_awaited_once_with(server, force_refresh=True)

    def manager_factory():
        return SimpleNamespace()

    get_version.reset_mock()
    assert await service.refresh_version_cache(
        server,
        ssh_manager_factory=manager_factory,  # type: ignore[arg-type]
    ) == (False, None)
    assert get_version.await_args.kwargs["ssh_manager_factory"] is manager_factory


@pytest.mark.asyncio
async def test_remote_map_sync_forwards_http_resource(monkeypatch) -> None:
    from services.map_management_service import DEFAULT_MAPS_CONFIG
    from services.remote_map_pool_service import synchronize_remote_map_pool

    manager = SimpleNamespace(
        execute_command=AsyncMock(return_value=(True, "", "")),
    )
    server = _server(122)
    http = SimpleNamespace(name="owned")
    fetch = AsyncMock(return_value=DEFAULT_MAPS_CONFIG)
    replace = AsyncMock()
    monkeypatch.setattr(
        "services.remote_map_pool_service.fetch_remote_map_pool",
        fetch,
    )
    monkeypatch.setattr(
        "services.remote_map_pool_service.replace_remote_map_pool",
        replace,
    )

    content, map_count = await synchronize_remote_map_pool(
        manager,
        server,
        "https://maps.example.com/maps.txt",
        http_resource=http,  # type: ignore[arg-type]
    )

    assert content == DEFAULT_MAPS_CONFIG
    assert map_count == 0
    fetch.assert_awaited_once_with(
        "https://maps.example.com/maps.txt",
        http_resource=http,
    )
