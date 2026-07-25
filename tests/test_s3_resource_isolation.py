"""S3 client caches must belong to one application factory instance."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from api.application import create_app
from api.dependencies import get_unit_of_work, resolve_s3_backup_service
from api.lifecycle import ApplicationLifecycle
from api.routes.actions import common as action_common
from api.routes.actions import deployment as action_deployment
from modules.auth import get_current_principal
from modules.config import settings as default_settings
from modules.models import AuthType, Server, User
from services.s3_backup_service import (
    S3_BACKUP_SERVICE_KEY,
    S3BackupService,
    s3_backup_service,
)


def _resources() -> dict[str, object]:
    return {
        "database": SimpleNamespace(
            engine=object(),
            session_factory=Mock(),
            close=AsyncMock(),
        ),
        "redis": SimpleNamespace(
            delete_by_pattern=AsyncMock(return_value=0),
            close=AsyncMock(),
        ),
        "http": SimpleNamespace(close=AsyncMock()),
        "ssh_pool": SimpleNamespace(
            start_cleanup=AsyncMock(),
            stop_cleanup=AsyncMock(),
            close_all=AsyncMock(),
        ),
        "task_supervisor": SimpleNamespace(start=Mock(), shutdown=AsyncMock()),
    }


def _request_for(app) -> Request:
    return Request({"type": "http", "app": app})


def test_isolated_apps_own_distinct_s3_client_caches() -> None:
    first_app = create_app(
        settings=default_settings.model_copy(),
        resource_overrides=_resources(),
        lifespan=None,
    )
    second_app = create_app(
        settings=default_settings.model_copy(),
        resource_overrides=_resources(),
        lifespan=None,
    )

    first = first_app.state.container.services[S3_BACKUP_SERVICE_KEY]
    second = second_app.state.container.services[S3_BACKUP_SERVICE_KEY]

    assert isinstance(first, S3BackupService)
    assert isinstance(second, S3BackupService)
    assert first is not second
    assert first is not s3_backup_service
    assert second is not s3_backup_service
    assert resolve_s3_backup_service(_request_for(first_app)) is first
    assert resolve_s3_backup_service(_request_for(second_app)) is second


def test_s3_dependency_fails_closed_without_an_application_service() -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(
            container=SimpleNamespace(services={}),
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        resolve_s3_backup_service(_request_for(app))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "S3 backup service is unavailable"


@pytest.mark.asyncio
async def test_plugin_backup_action_detaches_db_before_app_s3_io(monkeypatch) -> None:
    route = cast(
        APIRoute,
        next(
            route
            for route in action_deployment.router.routes
            if getattr(route, "path", None) == "/servers/{server_id}/actions"
        ),
    )
    assert resolve_s3_backup_service in {
        dependency.call for dependency in route.dependant.dependencies
    }

    owner = User(
        id=7,
        username="owner",
        email="owner@example.com",
        hashed_password="hash",
        s3_enabled=True,
        s3_bucket="backups",
        s3_access_key_id="access",
        s3_secret_access_key="secret",
    )
    server = Server(
        id=31,
        user_id=7,
        name="backup-target",
        host="server.example.com",
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
    )

    class Database:
        def __init__(self) -> None:
            self.committed = False

        async def commit(self) -> None:
            self.committed = True

    database = Database()
    ssh_manager = SimpleNamespace(last_plugin_backup={"path": "/remote/plugins.tar.gz"})

    async def upload(_ssh, detached_server, detached_owner, backup_path, **_kwargs):
        assert database.committed is True
        assert detached_server is not server
        assert detached_owner is not owner
        assert (detached_server.id, detached_owner.id) == (31, 7)
        assert backup_path == "/remote/plugins.tar.gz"
        return True, "uploaded", "user-7/server-31/plugins.tar.gz"

    service = SimpleNamespace(
        is_configured=lambda user: bool(user.s3_enabled),
        upload_remote_backup=AsyncMock(side_effect=upload),
    )
    notify = Mock()
    monkeypatch.setattr(action_common.discord_notification_service, "queue_notify", notify)

    result = await action_common.upload_latest_plugin_backup_to_s3(
        database,  # type: ignore[arg-type]
        server,
        owner,
        ssh_manager,  # type: ignore[arg-type]
        s3_service=service,  # type: ignore[arg-type]
    )

    assert result == (True, "uploaded")
    service.upload_remote_backup.assert_awaited_once()
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_auth_s3_probe_uses_only_each_apps_service() -> None:
    first_events: list[str] = []
    second_events: list[str] = []

    async def first_probe(configuration):
        assert first_events == ["commit"]
        assert configuration.id == 7
        first_events.append("s3")
        return True, "first", []

    async def second_probe(configuration):
        assert second_events == ["commit"]
        assert configuration.id == 7
        second_events.append("s3")
        return False, "second", []

    first_service = SimpleNamespace(
        close=AsyncMock(),
        test_connection=AsyncMock(side_effect=first_probe),
    )
    second_service = SimpleNamespace(
        close=AsyncMock(),
        test_connection=AsyncMock(side_effect=second_probe),
    )
    first_app = create_app(
        settings=default_settings.model_copy(),
        resource_overrides={
            **_resources(),
            "services": {S3_BACKUP_SERVICE_KEY: first_service},
        },
        lifespan=None,
    )
    second_app = create_app(
        settings=default_settings.model_copy(),
        resource_overrides={
            **_resources(),
            "services": {S3_BACKUP_SERVICE_KEY: second_service},
        },
        lifespan=None,
    )
    user = SimpleNamespace(
        id=7,
        s3_enabled=True,
        s3_endpoint_url="https://s3.example.com",
        s3_region="test-1",
        s3_bucket="backups",
        s3_access_key_id="access",
        s3_secret_access_key="secret",
        s3_prefix="cs2",
        s3_use_ssl=True,
        s3_retention_count=10,
    )
    first_uow = SimpleNamespace(
        session=SimpleNamespace(get=AsyncMock(return_value=user)),
        commit=AsyncMock(side_effect=lambda: first_events.append("commit")),
    )
    second_uow = SimpleNamespace(
        session=SimpleNamespace(get=AsyncMock(return_value=user)),
        commit=AsyncMock(side_effect=lambda: second_events.append("commit")),
    )
    first_app.dependency_overrides[get_current_principal] = lambda: SimpleNamespace(id=7)
    second_app.dependency_overrides[get_current_principal] = lambda: SimpleNamespace(id=7)
    first_app.dependency_overrides[get_unit_of_work] = lambda: first_uow
    second_app.dependency_overrides[get_unit_of_work] = lambda: second_uow

    first_transport = httpx.ASGITransport(app=first_app)
    second_transport = httpx.ASGITransport(app=second_app)
    async with (
        httpx.AsyncClient(transport=first_transport, base_url="http://first") as first_client,
        httpx.AsyncClient(transport=second_transport, base_url="http://second") as second_client,
    ):
        first_response = await first_client.post("/api/auth/s3-settings/test")
        second_response = await second_client.post("/api/auth/s3-settings/test")

    assert first_response.json() == {"success": True, "message": "first", "steps": []}
    assert second_response.json() == {"success": False, "message": "second", "steps": []}
    first_configuration = first_service.test_connection.await_args.args[0]
    second_configuration = second_service.test_connection.await_args.args[0]
    assert first_configuration.id == second_configuration.id == 7
    assert first_configuration is not user
    assert second_configuration is not user
    first_uow.commit.assert_awaited_once_with()
    second_uow.commit.assert_awaited_once_with()
    assert first_events == ["commit", "s3"]
    assert second_events == ["commit", "s3"]


@pytest.mark.asyncio
async def test_isolated_lifecycle_closes_only_its_s3_cache(monkeypatch) -> None:
    app = create_app(
        settings=default_settings.model_copy(),
        resource_overrides=_resources(),
        lifespan=None,
    )
    owned_service = app.state.container.services[S3_BACKUP_SERVICE_KEY]
    owned_close = AsyncMock()
    global_close = AsyncMock()
    monkeypatch.setattr(owned_service, "close", owned_close)
    monkeypatch.setattr(s3_backup_service, "close", global_close)

    a2s_service = app.state.container.services["a2s_cache"]
    monkeypatch.setattr(a2s_service, "start", AsyncMock())
    monkeypatch.setattr(a2s_service, "stop", AsyncMock())
    monkeypatch.setattr("api.lifecycle.require_database_current", AsyncMock())

    lifecycle = ApplicationLifecycle(container=app.state.container)
    await lifecycle.start()
    await lifecycle.stop()

    owned_close.assert_awaited_once_with()
    global_close.assert_not_awaited()
