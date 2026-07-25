from __future__ import annotations

import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api.routes.file_manager import common as file_common
from api.routes.file_manager import downloads as download_routes
from api.routes.file_manager import files as file_routes
from modules import AuthType, Server, get_current_active_user, get_db
from services.ssh_manager import SSHManager


class _Session:
    def __init__(self) -> None:
        self.commit_count = 0
        self.transaction_open = False

    async def commit(self) -> None:
        self.commit_count += 1
        self.transaction_open = False


class _DatabaseDependency:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []
        self.session_factory = object()

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

    async def get(self, *_args: Any, **_kwargs: Any):
        return True, {}, None

    async def post(self, *_args: Any, **_kwargs: Any):
        return True, {}, None

    async def download_file(self, *_args: Any, **_kwargs: Any):
        return True, None

    @asynccontextmanager
    async def borrow_client(self):
        yield object()


class _NoopLocks:
    @asynccontextmanager
    async def _hold(self):
        yield

    def get(self, *_args: Any, **_kwargs: Any):
        return self._hold()


class _TaskSupervisor:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[None]] = []

    def create(self, coroutine, *, name: str | None = None):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task


def _server() -> Server:
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
    )


def _app(
    pool: object,
    http_resource: object,
    database: _DatabaseDependency,
    *,
    include_downloads: bool = False,
) -> tuple[FastAPI, _TaskSupervisor]:
    supervisor = _TaskSupervisor()
    app = FastAPI()
    app.state.container = SimpleNamespace(
        ssh_pool=pool,
        http=http_resource,
        database=SimpleNamespace(session_factory=database.session_factory),
    )
    app.state.task_supervisor = supervisor
    app.include_router(download_routes.router if include_downloads else file_routes.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=3,
        is_admin=False,
    )
    app.dependency_overrides[download_routes.resolve_maintenance_lock_service] = _NoopLocks
    return app, supervisor


@pytest.fixture
def detached_server(monkeypatch) -> Server:
    source = _server()

    async def require_access(db, server_id, user, *, commit):
        assert server_id == source.id
        assert user.id == source.user_id
        assert commit is False
        db.transaction_open = True
        return source

    monkeypatch.setattr(file_common, "require_server_access", require_access)
    return source


@pytest.mark.asyncio
async def test_two_file_manager_apps_use_only_their_ssh_and_http_resources(
    monkeypatch,
    detached_server: Server,
) -> None:
    observed: list[tuple[str, str, bool]] = []

    async def list_directory(self, path, server):
        database = self.connection_pool.database
        session = database.sessions[-1]
        assert session.commit_count == 1
        assert session.transaction_open is False
        observed.append(
            (
                self.connection_pool.name,
                self.http_resource.name,
                server is not detached_server,
            )
        )
        return True, [], ""

    monkeypatch.setattr(SSHManager, "list_directory", list_directory)
    monkeypatch.setattr(SSHManager, "disconnect", AsyncMock())

    first_database = _DatabaseDependency()
    second_database = _DatabaseDependency()
    first_app, _ = _app(
        _Pool("first-pool", first_database),
        _HTTP("first-http"),
        first_database,
    )
    second_app, _ = _app(
        _Pool("second-pool", second_database),
        _HTTP("second-http"),
        second_database,
    )

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
            first_client.get("/servers/17/files"),
            second_client.get("/servers/17/files"),
        )

    assert first_response.status_code == second_response.status_code == 200
    assert (
        first_response.json()
        == second_response.json()
        == {
            "path": "/srv/game",
            "files": [],
        }
    )
    assert sorted(observed) == [
        ("first-pool", "first-http", True),
        ("second-pool", "second-http", True),
    ]


@pytest.mark.asyncio
async def test_background_task_factory_keeps_each_application_resources(
    monkeypatch,
    detached_server: Server,
) -> None:
    observed: list[tuple[str, str]] = []

    async def validate(self, *_args: Any, **_kwargs: Any):
        database = self.connection_pool.database
        assert database.sessions[-1].transaction_open is False
        return True, ""

    async def token(db, _user):
        db.transaction_open = True
        return None

    async def run_task(
        _task_id,
        _url,
        _destination_path,
        _target_path,
        _server_id,
        _user_id,
        _user_is_admin,
        _overwrite,
        _github_token,
        _session_factory,
        _lock_service,
        _http_resource,
        ssh_manager_factory,
    ):
        manager = ssh_manager_factory()
        observed.append(
            (
                manager.connection_pool.name,
                manager.http_resource.name,
            )
        )

    monkeypatch.setattr(SSHManager, "validate_path_within_base", validate)
    monkeypatch.setattr(SSHManager, "disconnect", AsyncMock())
    monkeypatch.setattr(download_routes, "get_effective_github_token", token)
    monkeypatch.setattr(download_routes, "_run_download_url_task", run_task)

    first_database = _DatabaseDependency()
    second_database = _DatabaseDependency()
    first_app, first_supervisor = _app(
        _Pool("first-pool", first_database),
        _HTTP("first-http"),
        first_database,
        include_downloads=True,
    )
    second_app, second_supervisor = _app(
        _Pool("second-pool", second_database),
        _HTTP("second-http"),
        second_database,
        include_downloads=True,
    )
    payload = {
        "url": "https://example.com/archive.zip",
        "destination_path": "/srv/game",
    }

    file_common.download_url_tasks.clear()
    file_common._download_url_task_refs.clear()
    try:
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
                first_client.post("/servers/17/files/download-url", json=payload),
                second_client.post("/servers/17/files/download-url", json=payload),
            )
        await asyncio.gather(*first_supervisor.tasks, *second_supervisor.tasks)
    finally:
        file_common.download_url_tasks.clear()
        file_common._download_url_task_refs.clear()

    assert first_response.status_code == second_response.status_code == 202
    assert sorted(observed) == [
        ("first-pool", "first-http"),
        ("second-pool", "second-http"),
    ]
    for database in (first_database, second_database):
        assert database.sessions[-1].commit_count == 2
        assert database.sessions[-1].transaction_open is False


@pytest.mark.asyncio
async def test_file_manager_fails_closed_without_application_ssh_pool(
    monkeypatch,
) -> None:
    remote_operation = AsyncMock()
    monkeypatch.setattr(SSHManager, "list_directory", remote_operation)
    database = _DatabaseDependency()
    app, _ = _app(None, _HTTP("application"), database)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/servers/17/files")

    assert response.status_code == 503
    assert response.json() == {"detail": "SSH connection pool is unavailable"}
    remote_operation.assert_not_awaited()


def test_file_manager_request_modules_have_no_noarg_ssh_manager_calls() -> None:
    route_directory = Path(file_common.__file__).parent
    offending_calls: list[str] = []

    for source_path in sorted(route_directory.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "SSHManager"
                and not node.args
                and not node.keywords
            ):
                offending_calls.append(f"{source_path.name}:{node.lineno}")

    assert offending_calls == []
