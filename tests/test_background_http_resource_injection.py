"""Background integrations must retain only their application's resources."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from api import lifecycle as lifecycle_module
from api.lifecycle import ApplicationLifecycle
from modules.models import ManagedPlugin, User
from services.a2s_cache_service import A2S_CACHE_SERVICE_KEY
from services.github_service import fetch_github_repo_info
from services.maintenance_lock import MAINTENANCE_LOCK_SERVICE_KEY
from services.plugin_auto_update_service import PluginAutoUpdateService
from services.s3_backup_service import S3_BACKUP_SERVICE_KEY


class _ApplicationHTTP:
    def __init__(self, name: str) -> None:
        self.name = name
        self.is_closed = False
        self.get_calls: list[str] = []
        self.close = AsyncMock()

    async def get(self, url: str, **_kwargs: Any):
        self.get_calls.append(url)
        if url.endswith("/releases/latest"):
            return (
                True,
                {
                    "id": 42,
                    "tag_name": "v2",
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {
                            "name": "plugin-v2.zip",
                            "browser_download_url": "https://github.com/acme/plugin/v2.zip",
                        }
                    ],
                },
                None,
            )
        return (
            True,
            {
                "name": f"plugin-{self.name}",
                "description": f"Repository from {self.name}",
                "owner": {"login": "acme"},
                "topics": ["cs2"],
                "html_url": "https://github.com/acme/plugin",
            },
            None,
        )

    async def post(self, *_args: Any, **_kwargs: Any):
        raise AssertionError("unexpected POST")

    async def download_file(self, *_args: Any, **_kwargs: Any):
        raise AssertionError("unexpected download")

    @asynccontextmanager
    async def borrow_client(self):
        yield SimpleNamespace()


class _DatabaseContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return None


@pytest.mark.asyncio
async def test_github_services_prefer_explicit_http_over_global_facade(monkeypatch) -> None:
    outbound_http = _ApplicationHTTP("owned")
    forbid_global = AsyncMock(
        side_effect=AssertionError("process-global HTTP facade must not be used")
    )
    monkeypatch.setattr("services.github_service.http_helper.get", forbid_global)
    monkeypatch.setattr("services.plugin_auto_update_service.http_helper.get", forbid_global)

    repository = await fetch_github_repo_info(
        "https://github.com/acme/plugin",
        http_resource=outbound_http,
    )
    item = ManagedPlugin(
        id=5,
        server_id=7,
        source_type="github",
        source_key="https://github.com/acme/plugin",
        display_name="Plugin",
        repo_url="https://github.com/acme/plugin",
        asset_glob="plugin-*.zip",
    )
    user = User(
        id=3,
        username="owner",
        email="owner@example.com",
        hashed_password="hash",
    )
    success, latest, error = await PluginAutoUpdateService(
        http_resource=outbound_http
    )._latest_github_release(item, user)

    assert repository["name"] == "acme-plugin-owned"
    assert success is True
    assert latest is not None
    assert latest["release_id"] == "42"
    assert error == ""
    assert outbound_http.get_calls == [
        "https://api.github.com/repos/acme/plugin",
        "https://api.github.com/repos/acme/plugin/releases/latest",
    ]
    forbid_global.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_service_default_remains_a_direct_python_facade(monkeypatch) -> None:
    legacy_get = AsyncMock(
        return_value=(
            True,
            {
                "name": "plugin",
                "description": "Legacy direct caller",
                "owner": {"login": "acme"},
            },
            None,
        )
    )
    monkeypatch.setattr("services.github_service.http_helper.get", legacy_get)

    result = await fetch_github_repo_info("https://github.com/acme/plugin")

    assert result["name"] == "acme-plugin"
    legacy_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_passes_container_http_ssh_and_lock_to_background_services(
    monkeypatch,
) -> None:
    http = _ApplicationHTTP("lifecycle")
    pool = SimpleNamespace(
        start_cleanup=AsyncMock(),
        stop_cleanup=AsyncMock(),
        close_all=AsyncMock(),
    )
    lock_service = SimpleNamespace(name="application-lock")
    a2s_service = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    s3_service = SimpleNamespace(close=AsyncMock())
    database = SimpleNamespace(
        engine=object(),
        session_factory=lambda: _DatabaseContext(),
        close=AsyncMock(),
    )
    redis = SimpleNamespace(
        delete_by_pattern=AsyncMock(return_value=0),
        close=AsyncMock(),
    )
    supervisor = SimpleNamespace(start=lambda: None, shutdown=AsyncMock())
    container = SimpleNamespace(
        database=database,
        redis=redis,
        http=http,
        ssh_pool=pool,
        task_supervisor=supervisor,
        legacy_runtime=True,
        services={
            A2S_CACHE_SERVICE_KEY: a2s_service,
            MAINTENANCE_LOCK_SERVICE_KEY: lock_service,
            S3_BACKUP_SERVICE_KEY: s3_service,
        },
    )

    from services.auto_update_service import auto_update_service
    from services.plugin_auto_update_service import plugin_auto_update_service
    from services.scheduled_task_service import scheduled_task_service
    from services.server_monitor import server_monitor
    from services.ssh_health_monitor import ssh_health_monitor
    from services.steam_inf_service import steam_inf_service

    auto_start = AsyncMock()
    plugin_start = AsyncMock()
    scheduled_start = AsyncMock()
    steam_inf_start = AsyncMock()
    monkeypatch.setattr(auto_update_service, "start", auto_start)
    monkeypatch.setattr(auto_update_service, "stop", AsyncMock())
    monkeypatch.setattr(plugin_auto_update_service, "start", plugin_start)
    monkeypatch.setattr(plugin_auto_update_service, "stop", AsyncMock())
    monkeypatch.setattr(scheduled_task_service, "start", scheduled_start)
    monkeypatch.setattr(scheduled_task_service, "stop", AsyncMock())
    monkeypatch.setattr(steam_inf_service, "start", steam_inf_start)
    monkeypatch.setattr(steam_inf_service, "stop", AsyncMock())
    monkeypatch.setattr(ssh_health_monitor, "start", AsyncMock())
    monkeypatch.setattr(ssh_health_monitor, "stop", AsyncMock())
    monkeypatch.setattr(server_monitor, "stop_all", AsyncMock())
    monkeypatch.setattr(
        lifecycle_module.Server,
        "get_all_with_panel_monitoring",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(lifecycle_module, "require_database_current", AsyncMock())
    monkeypatch.setattr(lifecycle_module, "_cleanup_runtime_tasks", AsyncMock())

    lifecycle = ApplicationLifecycle(container=container)  # type: ignore[arg-type]
    await lifecycle.start()

    auto_kwargs = auto_start.await_args.kwargs
    plugin_kwargs = plugin_start.await_args.kwargs
    scheduled_kwargs = scheduled_start.await_args.kwargs
    steam_inf_kwargs = steam_inf_start.await_args.kwargs
    assert auto_kwargs["steam_service"].http_adapter is http
    assert auto_kwargs["lock_service"] is lock_service
    assert plugin_kwargs["http_resource"] is http
    assert plugin_kwargs["lock_service"] is lock_service
    assert scheduled_kwargs["http_resource"] is http
    assert scheduled_kwargs["lock_service"] is lock_service
    assert scheduled_kwargs["s3_service"] is s3_service
    factories = {
        steam_inf_kwargs["ssh_manager_factory"],
        auto_kwargs["ssh_manager_factory"],
        plugin_kwargs["ssh_manager_factory"],
        scheduled_kwargs["ssh_manager_factory"],
    }
    assert len(factories) == 1
    manager = factories.pop()()
    assert manager.connection_pool is pool
    assert manager.http_resource is http

    await lifecycle.stop()
