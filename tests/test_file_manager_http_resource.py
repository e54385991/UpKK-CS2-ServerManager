"""Application-owned HTTP boundaries for remote file downloads."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api.routes.file_manager import common as file_common
from api.routes.file_manager import downloads as download_routes
from modules import get_current_active_user, get_db
from modules.models import AuthType, Server


class _Database:
    def __init__(self) -> None:
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1


class _TaskSupervisor:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[None]] = []

    def create(self, coroutine, *, name: str | None = None):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task


class _GitHubClient:
    def __init__(self, name: str, database: _Database | None = None) -> None:
        self.name = name
        self.database = database
        self.calls: list[str] = []

    async def get(self, url: str, **_kwargs: Any) -> httpx.Response:
        if self.database is not None:
            assert self.database.commit_count >= 1
        self.calls.append(url)
        request = httpx.Request("GET", url)
        if url.endswith("/zip"):
            return httpx.Response(
                status_code=302,
                headers={
                    "Location": (f"https://objects.githubusercontent.com/{self.name}-artifact.zip")
                },
                request=request,
            )
        return httpx.Response(
            status_code=200,
            json={"name": f"{self.name}-artifact", "expired": False},
            request=request,
        )


class _HTTPAdapter:
    def __init__(self, name: str, database: _Database | None = None) -> None:
        self.name = name
        self.is_closed = False
        self.client = _GitHubClient(name, database)

    async def get(self, *_args: Any, **_kwargs: Any):
        return True, {}, None

    async def post(self, *_args: Any, **_kwargs: Any):
        return True, {}, None

    @asynccontextmanager
    async def borrow_client(self):
        yield self.client


class _NoopLocks:
    @asynccontextmanager
    async def _hold(self):
        yield

    def get(self, *_args: Any, **_kwargs: Any):
        return self._hold()


class _ValidationSSH:
    async def validate_path_within_base(self, *_args: Any, **_kwargs: Any):
        return True, ""

    async def disconnect(self) -> None:
        return None


def _download_app(
    adapter: object,
    database: _Database,
    supervisor: _TaskSupervisor,
) -> FastAPI:
    app = FastAPI()
    app.state.container = SimpleNamespace(
        http=adapter,
        database=SimpleNamespace(session_factory=object()),
    )
    app.state.task_supervisor = supervisor
    app.include_router(download_routes.router)
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=7,
        is_admin=False,
    )
    app.dependency_overrides[download_routes.get_ssh_manager] = _ValidationSSH
    app.dependency_overrides[download_routes.resolve_maintenance_lock_service] = lambda: (
        _NoopLocks()
    )
    return app


@pytest.fixture(autouse=True)
def _clear_download_tasks():
    file_common.download_url_tasks.clear()
    file_common._download_url_task_refs.clear()
    yield
    file_common.download_url_tasks.clear()
    file_common._download_url_task_refs.clear()


@pytest.mark.asyncio
async def test_two_file_manager_apps_pass_only_their_http_adapter_to_background(
    monkeypatch,
) -> None:
    first_database = _Database()
    second_database = _Database()
    first_http = _HTTPAdapter("first", first_database)
    second_http = _HTTPAdapter("second", second_database)
    first_supervisor = _TaskSupervisor()
    second_supervisor = _TaskSupervisor()
    observed: list[tuple[str, str]] = []

    async def token(_db, _user):
        return "github-token"

    async def run_task(
        _task_id,
        url,
        _destination_path,
        _target_path,
        _server_id,
        _user_id,
        _user_is_admin,
        _overwrite,
        github_token,
        _session_factory,
        _lock_service,
        http_resource,
        _ssh_manager_factory,
    ):
        assert http_resource in (first_http, second_http)
        database = first_database if http_resource is first_http else second_database
        assert database.commit_count >= 1
        resolved_url, filename = await file_common._resolve_github_actions_artifact(
            url,
            github_token,
            http_resource=http_resource,
        )
        observed.append((http_resource.name, f"{resolved_url}|{filename}"))

    @asynccontextmanager
    async def forbid_global_http():
        raise AssertionError("global HTTP facade must not be used by request tasks")
        yield  # pragma: no cover

    monkeypatch.setattr(file_common.http_helper, "borrow_client", forbid_global_http)
    monkeypatch.setattr(
        download_routes,
        "get_server_for_user",
        AsyncMock(return_value=SimpleNamespace(game_directory="/srv/game")),
    )
    monkeypatch.setattr(download_routes, "get_effective_github_token", token)
    monkeypatch.setattr(download_routes, "SSHManager", _ValidationSSH)
    monkeypatch.setattr(download_routes, "_run_download_url_task", run_task)

    first_app = _download_app(first_http, first_database, first_supervisor)
    second_app = _download_app(second_http, second_database, second_supervisor)
    artifact_url = "https://github.com/acme/plugin/actions/runs/123/artifacts/456"
    payload = {"url": artifact_url, "destination_path": "/srv/game"}

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
            first_client.post("/servers/11/files/download-url", json=payload),
            second_client.post("/servers/11/files/download-url", json=payload),
        )

    await asyncio.gather(*first_supervisor.tasks, *second_supervisor.tasks)

    assert first_response.status_code == second_response.status_code == 202
    assert sorted(observed) == [
        (
            "first",
            ("https://objects.githubusercontent.com/first-artifact.zip|first-artifact.zip"),
        ),
        (
            "second",
            ("https://objects.githubusercontent.com/second-artifact.zip|second-artifact.zip"),
        ),
    ]
    assert len(first_http.client.calls) == len(second_http.client.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_kind", ("missing", "closed"))
async def test_file_download_route_fails_closed_without_live_app_http(
    resource_kind: str,
) -> None:
    database_started = False

    async def database_dependency():
        nonlocal database_started
        database_started = True
        yield _Database()

    resource: object = None
    if resource_kind == "closed":
        closed = _HTTPAdapter("closed")
        closed.is_closed = True
        resource = closed

    app = FastAPI()
    app.state.container = SimpleNamespace(http=resource)
    app.include_router(download_routes.router)
    app.dependency_overrides[get_db] = database_dependency
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=7,
        is_admin=False,
    )
    app.dependency_overrides[download_routes.resolve_maintenance_lock_service] = lambda: (
        _NoopLocks()
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/servers/11/files/download-url",
            json={
                "url": "https://example.com/archive.zip",
                "destination_path": "/srv/game",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Outbound HTTP client is unavailable"}
    assert database_started is False


@pytest.mark.asyncio
async def test_background_snapshot_commits_before_github_http(monkeypatch) -> None:
    database = _Database()
    adapter = _HTTPAdapter("ordered", database)
    server = Server(
        id=11,
        user_id=7,
        name="ordered",
        host="server.example",
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        game_directory="/srv/game",
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, _server_id):
            return server

        async def commit(self):
            await database.commit()

    class DownloadSSH:
        @staticmethod
        def archive_type_from_path(_path):
            return "zip"

        async def connect(self, _server):
            return True, ""

        async def download_url_to_file(self, *_args, **_kwargs):
            return True, ""

        async def disconnect(self):
            return None

    @asynccontextmanager
    async def forbid_global_http():
        raise AssertionError("global HTTP facade must not be used by request tasks")
        yield  # pragma: no cover

    monkeypatch.setattr(file_common.http_helper, "borrow_client", forbid_global_http)
    monkeypatch.setattr(file_common, "SSHManager", DownloadSSH)
    task_id = "ordered-download"
    file_common.download_url_tasks[task_id] = {
        "status": "pending",
        "target_path": None,
        "created_at": 1.0,
        "started_at": None,
        "completed_at": None,
        "message": None,
        "error": None,
    }
    file_common._download_url_task_refs[task_id] = object()

    await file_common._run_download_url_task(
        task_id,
        "https://github.com/acme/plugin/actions/runs/123/artifacts/456",
        "/srv/game",
        None,
        11,
        7,
        False,
        False,
        "github-token",
        lambda: Session(),
        _NoopLocks(),
        adapter,
    )

    assert database.commit_count == 1
    assert file_common.download_url_tasks[task_id]["status"] == "completed"
    assert adapter.client.calls == [
        "https://api.github.com/repos/acme/plugin/actions/artifacts/456",
        "https://api.github.com/repos/acme/plugin/actions/artifacts/456/zip",
    ]
