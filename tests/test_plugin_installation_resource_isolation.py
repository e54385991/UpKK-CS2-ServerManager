from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api.routes import github_plugins, plugin_market
from modules import (
    ArchiveAnalysisResponse,
    AuthType,
    GitHubPluginInstallRequest,
    GitHubPluginInstallResponse,
    MarketPlugin,
    PluginCategory,
    Server,
    get_current_active_user,
    get_db,
)
from services import plugin_installation
from services.ssh_manager import SSHManager


class _Session:
    def __init__(self) -> None:
        self.commit_count = 0
        self.transaction_open = False

    async def commit(self) -> None:
        self.commit_count += 1
        self.transaction_open = False

    async def rollback(self) -> None:
        self.transaction_open = False

    async def refresh(self, _value: object) -> None:
        self.transaction_open = True

    def add(self, _value: object) -> None:
        return None


class _DatabaseDependency:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    async def __call__(self):
        session = _Session()
        self.sessions.append(session)
        yield session


class _Pool:
    def __init__(self, name: str, database: _DatabaseDependency) -> None:
        self.name = name
        self.database = database


class _HTTP:
    def __init__(self, name: str) -> None:
        self.name = name
        self.is_closed = False
        self.download_calls: list[str] = []

    async def get(self, *_args: Any, **_kwargs: Any):
        return True, {}, None

    async def post(self, *_args: Any, **_kwargs: Any):
        return True, {}, None

    async def download_file(self, url: str, *_args: Any, **_kwargs: Any):
        self.download_calls.append(url)
        return False, f"{self.name} download stopped"

    @asynccontextmanager
    async def borrow_client(self):
        yield object()


class _ServiceSSH:
    def __init__(self, database: _Session, source_server: Server) -> None:
        self.database = database
        self.source_server = source_server
        self.server: Server | None = None
        self.disconnected = False

    async def connect(self, server: Server):
        assert self.database.commit_count == 1
        assert self.database.transaction_open is False
        assert server is not self.source_server
        self.server = server
        return True, "connected"

    async def execute_command(self, command: str, **_kwargs: Any):
        if command.startswith("test -d "):
            return True, "exists", ""
        raise AssertionError(f"unexpected SSH command after failed panel download: {command}")

    async def disconnect(self) -> None:
        self.disconnected = True


def _server(*, use_panel_proxy: bool = False) -> Server:
    return Server(
        id=17,
        user_id=3,
        name="isolated-server",
        host="server.example",
        ssh_port=2222,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        ssh_password="ssh-secret",
        credential_revision=4,
        game_directory="/srv/game",
        use_panel_proxy=use_panel_proxy,
    )


def _github_app(
    pool: object,
    http_resource: object,
    database: _DatabaseDependency,
) -> FastAPI:
    app = FastAPI()
    app.state.container = SimpleNamespace(ssh_pool=pool, http=http_resource)
    app.include_router(github_plugins.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=3,
        is_admin=False,
    )
    app.dependency_overrides[github_plugins.locked_server_operation] = lambda: _server()
    return app


@pytest.mark.asyncio
async def test_installation_service_uses_injected_resources_after_detaching_server(
    monkeypatch,
) -> None:
    source_server = _server(use_panel_proxy=True)
    assert source_server.id is not None
    database = _Session()
    user = SimpleNamespace(id=3, is_admin=False)
    manager = _ServiceSSH(database, source_server)
    http_resource = _HTTP("injected")

    async def load_server(_cls, _db, server_id, user_id):
        assert server_id == source_server.id
        assert user_id == source_server.user_id
        database.transaction_open = True
        return source_server

    def global_ssh_forbidden(*_args: Any, **_kwargs: Any):
        raise AssertionError("global SSH constructor must not be used")

    global_download = AsyncMock(side_effect=AssertionError("global HTTP must not be used"))
    monkeypatch.setattr(
        plugin_installation.Server,
        "get_by_id_and_user",
        classmethod(load_server),
    )
    monkeypatch.setattr(plugin_installation, "SSHManager", global_ssh_forbidden)
    monkeypatch.setattr(plugin_installation, "send_deployment_update", AsyncMock())
    monkeypatch.setattr(
        "modules.http_helper.http_helper.download_file",
        global_download,
    )

    result = await plugin_installation.install_github_plugin(
        source_server.id,
        GitHubPluginInstallRequest(
            download_url="https://github.com/acme/plugin/releases/download/v1/plugin.zip",
            record_installation=False,
            suppress_notification=True,
        ),
        database,  # type: ignore[arg-type]
        user,  # type: ignore[arg-type]
        ssh_manager=manager,  # type: ignore[arg-type]
        http_resource=http_resource,
    )

    assert result == GitHubPluginInstallResponse(
        success=False,
        message="Failed to download to panel server: injected download stopped",
    )
    assert http_resource.download_calls == [
        "https://github.com/acme/plugin/releases/download/v1/plugin.zip"
    ]
    assert manager.server is not source_server
    assert manager.disconnected is True
    global_download.assert_not_awaited()


@pytest.mark.asyncio
async def test_two_github_install_routes_forward_only_their_app_resources(
    monkeypatch,
) -> None:
    observed: list[tuple[str, str, str]] = []

    async def install_service(
        _server_id,
        _request,
        _db,
        _user,
        *,
        ssh_manager,
        http_resource,
    ):
        observed.append(
            (
                ssh_manager.connection_pool.name,
                ssh_manager.http_resource.name,
                http_resource.name,
            )
        )
        return GitHubPluginInstallResponse(success=True, message="installed")

    monkeypatch.setattr(github_plugins, "install_github_plugin_service", install_service)
    first_database = _DatabaseDependency()
    second_database = _DatabaseDependency()
    first_http = _HTTP("first-http")
    second_http = _HTTP("second-http")
    first_app = _github_app(
        _Pool("first-pool", first_database),
        first_http,
        first_database,
    )
    second_app = _github_app(
        _Pool("second-pool", second_database),
        second_http,
        second_database,
    )
    payload = {
        "download_url": "https://github.com/acme/plugin/releases/download/v1/plugin.zip",
        "suppress_notification": True,
    }

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url="http://first",
        ) as first_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app),
            base_url="http://second",
        ) as second_client,
    ):
        first_response, second_response = await asyncio.gather(
            first_client.post("/api/github-plugins/servers/17/install", json=payload),
            second_client.post("/api/github-plugins/servers/17/install", json=payload),
        )

    assert first_response.status_code == second_response.status_code == 200
    assert sorted(observed) == [
        ("first-pool", "first-http", "first-http"),
        ("second-pool", "second-http", "second-http"),
    ]


@pytest.mark.asyncio
async def test_github_install_route_fails_closed_without_app_ssh_pool(
    monkeypatch,
) -> None:
    install_service = AsyncMock()
    monkeypatch.setattr(github_plugins, "install_github_plugin_service", install_service)
    database = _DatabaseDependency()
    app = _github_app(None, _HTTP("application"), database)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/github-plugins/servers/17/install",
            json={
                "download_url": ("https://github.com/acme/plugin/releases/download/v1/plugin.zip")
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "SSH connection pool is unavailable"}
    install_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_install_route_fails_closed_without_app_http(
    monkeypatch,
) -> None:
    install_service = AsyncMock()
    monkeypatch.setattr(github_plugins, "install_github_plugin_service", install_service)
    database = _DatabaseDependency()
    app = _github_app(_Pool("application", database), None, database)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/github-plugins/servers/17/install",
            json={
                "download_url": ("https://github.com/acme/plugin/releases/download/v1/plugin.zip")
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Outbound HTTP client is unavailable"}
    install_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_github_install_call_keeps_legacy_positional_signature(
    monkeypatch,
) -> None:
    direct_manager = SimpleNamespace(disconnect=AsyncMock())
    observed: list[tuple[object, object]] = []

    async def install_service(
        _server_id,
        _request,
        _db,
        _user,
        *,
        ssh_manager,
        http_resource,
    ):
        observed.append((ssh_manager, http_resource))
        return GitHubPluginInstallResponse(success=True, message="direct")

    monkeypatch.setattr(github_plugins, "SSHManager", lambda: direct_manager)
    monkeypatch.setattr(github_plugins, "install_github_plugin_service", install_service)

    result = await github_plugins.install_github_plugin(
        17,
        GitHubPluginInstallRequest(
            download_url="https://github.com/acme/plugin/releases/download/v1/plugin.zip"
        ),
        object(),  # type: ignore[arg-type]
        SimpleNamespace(id=3, is_admin=False),  # type: ignore[arg-type]
    )

    assert result.message == "direct"
    assert observed == [(direct_manager, None)]


@pytest.mark.asyncio
async def test_market_install_reuses_injected_resources_after_each_db_phase(
    monkeypatch,
) -> None:
    database = _DatabaseDependency()
    pool = _Pool("market-pool", database)
    http_resource = _HTTP("market-http")
    plugin = MarketPlugin(
        id=9,
        github_url="https://github.com/acme/plugin",
        title="Plugin",
        category=PluginCategory.OTHER,
    )
    server = _server()
    observed: list[tuple[str, str, str]] = []

    async def load_plugin(_cls, _db, plugin_id):
        assert plugin_id == plugin.id
        return plugin

    async def load_server(server_id, db, _user):
        assert server_id == server.id
        db.transaction_open = True
        await db.commit()
        return Server.model_validate(server, from_attributes=True)

    async def connect(self, detached_server):
        session = self.connection_pool.database.sessions[-1]
        assert session.transaction_open is False
        assert detached_server is not server
        observed.append(
            (
                self.connection_pool.name,
                self.http_resource.name,
                "preflight",
            )
        )
        return True, "connected"

    async def install_service(
        _server_id,
        _request,
        db,
        _user,
        *,
        ssh_manager,
        http_resource,
    ):
        # The real service performs its short server lookup and commit here.
        await db.commit()
        assert db.transaction_open is False
        observed.append(
            (
                ssh_manager.connection_pool.name,
                ssh_manager.http_resource.name,
                http_resource.name,
            )
        )
        return GitHubPluginInstallResponse(success=False, message="stopped")

    monkeypatch.setattr(
        plugin_market.MarketPlugin,
        "get_by_id",
        classmethod(load_plugin),
    )
    monkeypatch.setattr(plugin_market, "get_server_for_user", load_server)
    monkeypatch.setattr(plugin_market, "install_github_plugin", install_service)
    monkeypatch.setattr(SSHManager, "connect", connect)
    monkeypatch.setattr(SSHManager, "disconnect", AsyncMock())

    app = FastAPI()
    app.state.container = SimpleNamespace(ssh_pool=pool, http=http_resource)
    app.include_router(plugin_market.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=3,
        is_admin=False,
    )
    app.dependency_overrides[plugin_market.locked_server_operation] = lambda: server

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://market",
    ) as client:
        response = await client.post(
            "/api/plugin-market/plugins/9/install",
            params={
                "server_id": 17,
                "download_url": ("https://github.com/acme/plugin/releases/download/v1/plugin.zip"),
            },
        )

    assert response.status_code == 200
    assert response.json()["message"] == "stopped"
    assert observed == [
        ("market-pool", "market-http", "preflight"),
        ("market-pool", "market-http", "market-http"),
    ]


@pytest.mark.asyncio
async def test_market_archive_analysis_forwards_each_apps_ssh_resource(
    monkeypatch,
) -> None:
    plugin = MarketPlugin(
        id=9,
        github_url="https://github.com/acme/plugin",
        title="Plugin",
        category=PluginCategory.OTHER,
    )
    server = _server()
    observed_pools: list[str] = []

    async def load_plugin(_cls, _db, plugin_id):
        assert plugin_id == plugin.id
        return plugin

    async def load_server(server_id, _db, _user):
        assert server_id == server.id
        return Server.model_validate(server, from_attributes=True)

    async def analyze_archive(
        *,
        server_id,
        download_url,
        db,
        current_user,
        ssh_manager,
    ):
        del db, current_user
        assert server_id == server.id
        assert download_url.endswith("/plugin.zip")
        observed_pools.append(ssh_manager.connection_pool.name)
        return ArchiveAnalysisResponse(success=True)

    monkeypatch.setattr(
        plugin_market.MarketPlugin,
        "get_by_id",
        classmethod(load_plugin),
    )
    monkeypatch.setattr(plugin_market, "get_server_for_user", load_server)
    monkeypatch.setattr(github_plugins, "analyze_archive", analyze_archive)

    def app(name: str) -> FastAPI:
        database = _DatabaseDependency()
        result = FastAPI()
        result.state.container = SimpleNamespace(
            ssh_pool=_Pool(name, database),
            http=_HTTP(f"{name}-http"),
        )
        result.include_router(plugin_market.router)
        result.dependency_overrides[get_db] = database
        result.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
            id=3,
            is_admin=False,
        )
        return result

    first_app = app("first-pool")
    second_app = app("second-pool")
    params = {
        "server_id": 17,
        "download_url": "https://github.com/acme/plugin/releases/download/v1/plugin.zip",
    }

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url="http://first",
        ) as first_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app),
            base_url="http://second",
        ) as second_client,
    ):
        first_response, second_response = await asyncio.gather(
            first_client.get("/api/plugin-market/plugins/9/analyze-archive", params=params),
            second_client.get("/api/plugin-market/plugins/9/analyze-archive", params=params),
        )

    assert first_response.status_code == second_response.status_code == 200
    assert sorted(observed_pools) == ["first-pool", "second-pool"]
