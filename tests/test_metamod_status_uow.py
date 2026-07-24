from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.dependencies import get_ssh_manager
from api.routes.actions import status as status_routes
from cs2_manager.core import Principal
from cs2_manager.features.actions import (
    MetamodServerTarget,
    MetamodStatusResult,
    MetamodStatusService,
    ServerActionRepository,
    ServerNotFoundError,
)
from cs2_manager.infrastructure import UnitOfWork
from modules import AuthType, MetamodStatusResponse, Server


def _principal(*, user_id: int = 7, is_admin: bool = False) -> Principal:
    return Principal(
        id=user_id,
        username="admin" if is_admin else "owner",
        email="user@example.com",
        is_admin=is_admin,
    )


def _server(*, user_id: int = 7) -> Server:
    return Server(
        id=17,
        user_id=user_id,
        name="metamod-target",
        host="server.example",
        ssh_port=2222,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        ssh_password="ssh-secret",
        ssh_key_path=None,
        credential_revision=4,
        ssh_host_key_algorithm="ssh-ed25519",
        ssh_host_key_fingerprint="SHA256:confirmed",
        is_ssh_down=False,
        game_directory="/srv/cs2 folder",
    )


def _target() -> MetamodServerTarget:
    return MetamodServerTarget(
        id=17,
        host="server.example",
        ssh_port=2222,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        credential_revision=4,
        ssh_password="ssh-secret",
        ssh_key_path=None,
        ssh_host_key_algorithm="ssh-ed25519",
        ssh_host_key_fingerprint="SHA256:confirmed",
        is_ssh_down=False,
        game_directory="/srv/cs2 folder",
    )


class _ServerResult:
    def __init__(self, server: Server | None) -> None:
        self.server = server

    def scalar_one_or_none(self) -> Server | None:
        return self.server


class _RepositorySession:
    def __init__(self, server: Server | None) -> None:
        self.server = server
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _ServerResult(self.server)


@pytest.mark.asyncio
async def test_repository_filters_owner_but_allows_admin_and_returns_snapshot() -> None:
    owner_session = _RepositorySession(_server())
    owner_target = await ServerActionRepository(  # type: ignore[arg-type]
        owner_session
    ).require_metamod_target(17, _principal())
    owner_filter = str(owner_session.statements[0].whereclause)

    admin_session = _RepositorySession(_server(user_id=99))
    admin_target = await ServerActionRepository(  # type: ignore[arg-type]
        admin_session
    ).require_metamod_target(17, _principal(user_id=1, is_admin=True))
    admin_filter = str(admin_session.statements[0].whereclause)

    assert "servers.user_id" in owner_filter
    assert "servers.user_id" not in admin_filter
    assert owner_target == _target()
    assert admin_target.id == 17
    assert "ssh-secret" not in repr(owner_target)
    assert owner_target.is_password_auth is True
    assert owner_target.is_key_auth is False


@pytest.mark.asyncio
async def test_repository_maps_invisible_server_to_domain_error() -> None:
    session = _RepositorySession(None)

    with pytest.raises(ServerNotFoundError, match="Server not found"):
        await ServerActionRepository(  # type: ignore[arg-type]
            session
        ).require_metamod_target(404, _principal())


@pytest.mark.asyncio
async def test_repository_rejects_unpersisted_server_snapshot() -> None:
    server = _server()
    server.id = None
    session = _RepositorySession(server)

    with pytest.raises(RuntimeError, match="missing its id"):
        await ServerActionRepository(  # type: ignore[arg-type]
            session
        ).require_metamod_target(17, _principal())


class _Cache:
    def __init__(
        self,
        events: list[str] | None = None,
        *,
        cached: object = None,
        get_error: Exception | None = None,
        set_error: Exception | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.cached = cached
        self.get_error = get_error
        self.set_error = set_error
        self.set_calls: list[tuple[str, object, int]] = []

    async def get(self, key: str) -> object:
        self.events.append("cache_get")
        if self.get_error is not None:
            raise self.get_error
        return self.cached

    async def set(self, key: str, value: object, expire: int = 300) -> bool:
        self.events.append("cache_set")
        if self.set_error is not None:
            raise self.set_error
        self.set_calls.append((key, value, expire))
        return True


class _SSHManager:
    def __init__(
        self,
        events: list[str],
        *,
        connect_result: tuple[bool, str] = (True, "connected"),
        execute_result: tuple[bool, str, str] = (True, "exists\n", ""),
        execute_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.connect_result = connect_result
        self.execute_result = execute_result
        self.execute_error = execute_error
        self.target: MetamodServerTarget | None = None
        self.commands: list[str] = []

    async def connect(self, target: MetamodServerTarget) -> tuple[bool, str]:
        self.events.append("ssh_connect")
        self.target = target
        return self.connect_result

    async def execute_command(self, command: str) -> tuple[bool, str, str]:
        self.events.append("ssh_execute")
        self.commands.append(command)
        if self.execute_error is not None:
            raise self.execute_error
        return self.execute_result

    async def disconnect(self) -> None:
        self.events.append("ssh_disconnect")


@pytest.mark.asyncio
async def test_service_uses_valid_cached_payload_without_creating_ssh_manager() -> None:
    payload = (
        b'{"success": true, "installed": true, "path": "/cached", '
        b'"message": "cached", "error": null}'
    )
    cache = _Cache(cached=payload)

    service = MetamodStatusService(
        cache,
        lambda: pytest.fail("cached status must not create an SSH manager"),
    )

    assert await service.get_status(_target()) == MetamodStatusResult(
        success=True,
        installed=True,
        path="/cached",
        message="cached",
    )


@pytest.mark.asyncio
async def test_service_quotes_path_caches_result_and_disconnects() -> None:
    events: list[str] = []
    cache = _Cache(events)
    manager = _SSHManager(events)
    service = MetamodStatusService(cache, lambda: manager)

    result = await service.get_status(_target())

    assert result.success is True
    assert result.installed is True
    assert result.path is not None
    assert manager.commands == [
        "test -f '/srv/cs2 folder/cs2/game/csgo/addons/metamod/"
        "bin/linuxsteamrt64/metamod.2.cs2.so' && echo 'exists'"
    ]
    assert events == [
        "cache_get",
        "ssh_connect",
        "ssh_execute",
        "cache_set",
        "ssh_disconnect",
    ]
    assert cache.set_calls[0][0] == "metamod_status:server:17"
    assert cache.set_calls[0][2] == 3600


@pytest.mark.asyncio
async def test_service_tolerates_cache_errors_and_releases_failed_ssh() -> None:
    events: list[str] = []
    cache = _Cache(events, get_error=RuntimeError("redis unavailable"))
    manager = _SSHManager(events, connect_result=(False, "offline"))

    result = await MetamodStatusService(cache, lambda: manager).get_status(_target())

    assert result == MetamodStatusResult(
        success=False,
        installed=False,
        error="Failed to connect via SSH: offline",
    )
    assert events == ["cache_get", "ssh_connect", "ssh_disconnect"]


@pytest.mark.asyncio
async def test_service_returns_error_and_disconnects_when_command_fails() -> None:
    events: list[str] = []
    cache = _Cache(events, set_error=RuntimeError("unused"))
    manager = _SSHManager(events, execute_error=RuntimeError("channel closed"))

    result = await MetamodStatusService(cache, lambda: manager).get_status(_target())

    assert result == MetamodStatusResult(
        success=False,
        installed=False,
        error="Error checking metamod status: channel closed",
    )
    assert events[-1] == "ssh_disconnect"


@pytest.mark.asyncio
async def test_service_keeps_live_result_when_cache_write_fails() -> None:
    events: list[str] = []
    cache = _Cache(events, set_error=RuntimeError("redis unavailable"))
    manager = _SSHManager(events, execute_result=(True, "", ""))

    result = await MetamodStatusService(cache, lambda: manager).get_status(_target())

    assert result == MetamodStatusResult(
        success=True,
        installed=False,
        message="Metamod:Source is not installed",
    )
    assert events == [
        "cache_get",
        "ssh_connect",
        "ssh_execute",
        "cache_set",
        "ssh_disconnect",
    ]


class _OrderingSession(_RepositorySession):
    def __init__(self, events: list[str], server: Server | None) -> None:
        super().__init__(server)
        self.events = events

    async def execute(self, statement):
        self.events.append("db_query")
        return await super().execute(statement)

    async def commit(self) -> None:
        self.events.append("db_commit")

    async def rollback(self) -> None:
        self.events.append("db_rollback")

    async def close(self) -> None:
        self.events.append("db_close")


class _Pool:
    async def acquire_lease(self, _server):
        raise AssertionError("the fake SSH manager owns this test boundary")


def _request(*, cache: object, pool: object, database: object | None = None) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            container=SimpleNamespace(
                redis=cache,
                ssh_pool=pool,
                database=database,
            )
        )
    )
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_route_commits_uow_before_cache_and_ssh_io() -> None:
    events: list[str] = []
    session = _OrderingSession(events, _server())
    cache = _Cache(events)
    pool = _Pool()
    manager = _SSHManager(events)

    async with UnitOfWork(lambda: session) as uow:  # type: ignore[arg-type]
        response = await status_routes.get_metamod_status(
            request=_request(cache=cache, pool=pool),
            server_id=17,
            ssh_manager=manager,  # type: ignore[arg-type]
            uow=uow,
            current_user=_principal(),
        )

    assert isinstance(response, MetamodStatusResponse)
    assert response.installed is True
    assert isinstance(manager.target, MetamodServerTarget)
    assert events[:4] == ["db_query", "db_commit", "cache_get", "ssh_connect"]
    assert events[-1] == "db_close"


@pytest.mark.asyncio
async def test_route_maps_domain_not_found_without_remote_io() -> None:
    events: list[str] = []
    session = _OrderingSession(events, None)
    cache = _Cache(events)

    async with UnitOfWork(lambda: session) as uow:  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as exc_info:
            await status_routes.get_metamod_status(
                request=_request(cache=cache, pool=_Pool()),
                server_id=404,
                ssh_manager=_SSHManager(events),  # type: ignore[arg-type]
                uow=uow,
                current_user=_principal(),
            )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Server not found"
    assert "db_commit" not in events
    assert "cache_get" not in events
    assert "db_rollback" in events


@pytest.mark.asyncio
async def test_route_fails_closed_when_application_cache_is_missing() -> None:
    session = _OrderingSession([], _server())

    async with UnitOfWork(lambda: session) as uow:  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as exc_info:
            await status_routes.get_metamod_status(
                request=_request(cache=None, pool=_Pool()),
                server_id=17,
                ssh_manager=_SSHManager([]),  # type: ignore[arg-type]
                uow=uow,
                current_user=_principal(),
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Metamod status cache is unavailable"
    assert session.statements == []


def test_ssh_provider_fails_closed_without_application_pool() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_ssh_manager(_request(cache=_Cache(), pool=None))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "SSH connection pool is unavailable"


@pytest.mark.asyncio
async def test_uow_dependency_fails_closed_without_application_database() -> None:
    dependency = status_routes.get_unit_of_work(
        _request(cache=_Cache(), pool=_Pool(), database=None)
    )

    with pytest.raises(HTTPException) as exc_info:
        await anext(dependency)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Database unit of work is unavailable"


@pytest.mark.asyncio
async def test_uow_dependency_yields_and_closes_application_owned_uow() -> None:
    events: list[str] = []
    session = _OrderingSession(events, _server())
    database = SimpleNamespace(unit_of_work=lambda: UnitOfWork(lambda: session))
    dependency = status_routes.get_unit_of_work(
        _request(cache=_Cache(), pool=_Pool(), database=database)
    )

    uow = await anext(dependency)
    assert uow.session is session
    await dependency.aclose()

    assert events == ["db_rollback", "db_close"]


def test_inactive_uow_has_no_route_session() -> None:
    uow = UnitOfWork(lambda: _OrderingSession([], _server()))

    with pytest.raises(RuntimeError, match="not active"):
        status_routes._uow_session(uow)


def test_route_declares_principal_uow_and_precise_response_contract() -> None:
    route = next(
        route
        for route in status_routes.router.routes
        if route.path == "/servers/{server_id}/metamod-status"
    )
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}

    assert status_routes.get_unit_of_work in dependency_calls
    assert get_ssh_manager in dependency_calls
    assert status_routes.get_current_principal in dependency_calls
    assert route.response_model is MetamodStatusResponse
    assert route.status_code == 200
    assert set(route.responses) == {401, 404, 503}


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        ("not-json", None),
        ({"success": "yes", "installed": False}, None),
        ({"success": True}, None),
        (42, None),
        (
            {
                "success": False,
                "installed": False,
                "error": 17,
            },
            MetamodStatusResult(
                success=False,
                installed=False,
                error="17",
            ),
        ),
    ),
)
def test_cached_payload_validation(payload, expected) -> None:
    assert MetamodStatusResult.from_payload(payload) == expected
