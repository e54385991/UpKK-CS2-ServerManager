"""Application-owned outbound HTTP adapters for API integrations."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from api.routes import github_plugins, map_management, plugin_market
from modules import get_current_active_user, get_current_admin_user, get_db


class _Session:
    def __init__(self) -> None:
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        return None


class _DatabaseDependency:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    async def __call__(self):
        session = _Session()
        self.sessions.append(session)
        yield session


class _HTTPAdapter:
    def __init__(self, name: str, database: _DatabaseDependency | None = None) -> None:
        self.name = name
        self.database = database
        self.is_closed = False
        self.calls: list[tuple[str, str]] = []

    async def get(self, url: str, **_kwargs: Any):
        if self.database is not None:
            assert self.database.sessions[-1].commit_count >= 1
        self.calls.append(("GET", url))
        if url.endswith("/readme"):
            return False, None, "README unavailable"
        if url.endswith("/repos/acme/plugin"):
            return (
                True,
                {
                    "name": f"plugin-{self.name}",
                    "description": f"Repository from {self.name}",
                },
                None,
            )
        return (
            True,
            [
                {
                    "id": f"{self.name}-release",
                    "tag_name": f"v-{self.name}",
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {
                            "name": f"{self.name}.tar.gz",
                            "browser_download_url": (
                                "https://github.com/acme/plugin/releases/download/"
                                f"v-{self.name}/{self.name}.tar.gz"
                            ),
                            "size": 42,
                            "content_type": "application/gzip",
                        }
                    ],
                }
            ],
            None,
        )

    async def post(self, url: str, **_kwargs: Any):
        self.calls.append(("POST", url))
        return (
            True,
            {"response": {"publishedfiledetails": [{"title": f"Workshop from {self.name}"}]}},
            None,
        )

    @asynccontextmanager
    async def borrow_client(self):
        yield SimpleNamespace()


def _app(http_resource: object, database: _DatabaseDependency) -> FastAPI:
    app = FastAPI()
    app.state.container = SimpleNamespace(http=http_resource)

    async def active_user():
        return SimpleNamespace(id=7, is_admin=False)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_active_user] = active_user
    app.include_router(github_plugins.router)
    return app


def _plugin_app(http_resource: object, database: _DatabaseDependency) -> FastAPI:
    app = FastAPI()
    app.state.container = SimpleNamespace(http=http_resource)

    async def admin_user():
        return SimpleNamespace(id=1, username="admin", is_admin=True)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_admin_user] = admin_user
    app.include_router(plugin_market.router)
    return app


@pytest.mark.asyncio
async def test_two_apps_never_share_github_http_adapters(monkeypatch) -> None:
    async def token(_db, _user):
        return "token"

    monkeypatch.setattr(github_plugins, "get_effective_github_token", token)
    first_database = _DatabaseDependency()
    second_database = _DatabaseDependency()
    first_http = _HTTPAdapter("first", first_database)
    second_http = _HTTPAdapter("second", second_database)
    first_app = _app(first_http, first_database)
    second_app = _app(second_http, second_database)

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
        first_response = await first_client.get(
            "/api/github-plugins/releases",
            params={"repo_url": "https://github.com/acme/plugin"},
        )
        second_response = await second_client.get(
            "/api/github-plugins/releases",
            params={"repo_url": "https://github.com/acme/plugin"},
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["releases"][0]["tag_name"] == "v-first"
    assert second_response.json()["releases"][0]["tag_name"] == "v-second"
    assert len(first_http.calls) == len(second_http.calls) == 1
    assert first_database.sessions[0].commit_count == 1
    assert second_database.sessions[0].commit_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_kind", ("missing", "closed"))
async def test_http_backed_routes_fail_closed_when_app_adapter_is_unavailable(
    monkeypatch,
    resource_kind: str,
) -> None:
    async def token(_db, _user):
        raise AssertionError("database work must not start without the HTTP resource")

    monkeypatch.setattr(github_plugins, "get_effective_github_token", token)
    database = _DatabaseDependency()
    resource: object = None
    if resource_kind == "closed":
        closed = _HTTPAdapter("closed")
        closed.is_closed = True
        resource = closed
    app = _app(resource, database)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/github-plugins/releases",
            params={"repo_url": "https://github.com/acme/plugin"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Outbound HTTP client is unavailable"}


@pytest.mark.asyncio
async def test_plugin_and_map_helpers_use_explicit_http_adapter(monkeypatch) -> None:
    adapter = _HTTPAdapter("isolated")

    async def forbid_global(*_args, **_kwargs):
        raise AssertionError("global HTTP facade must not be used")

    monkeypatch.setattr(plugin_market.http_helper, "get", forbid_global)
    monkeypatch.setattr(map_management.http_helper, "post", forbid_global)

    repository = await plugin_market.fetch_github_repo_info(
        "https://github.com/acme/plugin",
        http_resource=adapter,
    )
    workshop_title = await map_management._fetch_workshop_title(
        "3298427415",
        http_resource=adapter,
    )

    assert repository.success is True
    assert repository.repo_name == "plugin-isolated"
    assert workshop_title == "Workshop from isolated"
    assert adapter.calls[-1][0] == "POST"


@pytest.mark.asyncio
async def test_plugin_market_route_uses_its_app_http_after_releasing_db(monkeypatch) -> None:
    async def token(_db, _user):
        return "admin-token"

    async def forbid_global(*_args, **_kwargs):
        raise AssertionError("global HTTP facade must not be used")

    monkeypatch.setattr(plugin_market, "get_effective_github_token", token)
    monkeypatch.setattr(plugin_market.http_helper, "get", forbid_global)
    database = _DatabaseDependency()
    adapter = _HTTPAdapter("market", database)
    app = _plugin_app(adapter, database)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://market",
    ) as client:
        response = await client.post(
            "/api/plugin-market/fetch-repo-info",
            params={"github_url": "https://github.com/acme/plugin"},
        )

    assert response.status_code == 200
    assert response.json()["repo_name"] == "plugin-market"
    assert database.sessions[0].commit_count == 1
    assert [method for method, _url in adapter.calls] == ["GET", "GET"]


def test_http_backed_routes_publish_response_and_error_models() -> None:
    database = _DatabaseDependency()
    schema = _app(_HTTPAdapter("schema"), database).openapi()

    release_operation = schema["paths"]["/api/github-plugins/releases"]["get"]
    assert release_operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GitHubReleasesResponse"
    }
    assert release_operation["responses"]["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }

    map_routes = {
        (route.path, next(iter(route.methods))): route for route in map_management.router.routes
    }
    assert (
        map_routes[("/servers/{server_id}/maps/custom-sync/run", "POST")].response_model
        is map_management.CustomMapSyncRunResponse
    )
    assert map_routes[("/servers/{server_id}/maps/preset", "POST")].response_model is (
        map_management.MapPresetResponse
    )
    assert map_routes[("/servers/{server_id}/maps", "POST")].response_model is (
        map_management.MapAddResponse
    )
