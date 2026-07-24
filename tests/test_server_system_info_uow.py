from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.dependencies import get_ssh_manager
from api.routes.servers import maintenance
from cs2_manager.core import Principal
from cs2_manager.features.servers import (
    CPUCountResponse,
    CPUCountResult,
    CPUCountService,
    DeploymentCheckResponse,
    DeploymentCheckResult,
    DeploymentCheckService,
    DiskSpaceFailureResponse,
    DiskSpaceInfo,
    DiskSpaceResponse,
    DiskSpaceResult,
    DiskSpaceService,
    DiskSpaceSuccessResponse,
    ServerSystemInfoNotFoundError,
    ServerSystemInfoRepository,
    ServerSystemInfoTarget,
    cpu_count_response,
    deployment_check_response,
    disk_space_response,
)
from cs2_manager.infrastructure import UnitOfWork
from modules import AuthType, Server


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
        name="system-info-target",
        host="server.example",
        ssh_port=2222,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        ssh_password="ssh-secret",
        credential_revision=4,
        ssh_host_key_algorithm="ssh-ed25519",
        ssh_host_key_fingerprint="SHA256:confirmed",
        is_ssh_down=False,
        game_directory="/srv/cs2 folder",
    )


def _target() -> ServerSystemInfoTarget:
    return ServerSystemInfoTarget(
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


class _Session:
    def __init__(self, server: Server | None, events: list[str] | None = None) -> None:
        self.server = server
        self.events = events if events is not None else []
        self.statements: list[object] = []

    async def execute(self, statement):
        self.events.append("db_query")
        self.statements.append(statement)
        return _ServerResult(self.server)

    async def commit(self) -> None:
        self.events.append("db_commit")

    async def rollback(self) -> None:
        self.events.append("db_rollback")

    async def close(self) -> None:
        self.events.append("db_close")


@pytest.mark.asyncio
async def test_repository_filters_owner_allows_admin_and_detaches_credentials() -> None:
    owner_session = _Session(_server())
    owner_target = await ServerSystemInfoRepository(  # type: ignore[arg-type]
        owner_session
    ).require_target(17, _principal())
    owner_filter = str(owner_session.statements[0].whereclause)

    admin_session = _Session(_server(user_id=99))
    admin_target = await ServerSystemInfoRepository(  # type: ignore[arg-type]
        admin_session
    ).require_target(17, _principal(user_id=1, is_admin=True))
    admin_filter = str(admin_session.statements[0].whereclause)

    assert "servers.user_id" in owner_filter
    assert "servers.user_id" not in admin_filter
    assert owner_target == _target()
    assert admin_target.id == 17
    assert "ssh-secret" not in repr(owner_target)
    assert owner_target.is_password_auth is True
    assert owner_target.is_key_auth is False


@pytest.mark.asyncio
async def test_repository_rejects_invisible_and_unpersisted_servers() -> None:
    with pytest.raises(ServerSystemInfoNotFoundError, match="Server not found"):
        await ServerSystemInfoRepository(  # type: ignore[arg-type]
            _Session(None)
        ).require_target(404, _principal())

    unpersisted = _server()
    unpersisted.id = None
    with pytest.raises(RuntimeError, match="missing its id"):
        await ServerSystemInfoRepository(  # type: ignore[arg-type]
            _Session(unpersisted)
        ).require_target(17, _principal())


class _Manager:
    def __init__(
        self,
        events: list[str] | None = None,
        *,
        connect_result: tuple[bool, str] = (True, "connected"),
        execute_results: list[tuple[bool, str, str] | Exception] | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.connect_result = connect_result
        self.execute_results = list(execute_results or [])
        self.connect_error = connect_error
        self.target: ServerSystemInfoTarget | None = None
        self.commands: list[tuple[str, int]] = []

    async def connect(self, target: ServerSystemInfoTarget) -> tuple[bool, str]:
        self.events.append("ssh_connect")
        self.target = target
        if self.connect_error is not None:
            raise self.connect_error
        return self.connect_result

    async def execute_command(
        self,
        command: str,
        timeout: int = 30,
    ) -> tuple[bool, str, str]:
        self.events.append("ssh_execute")
        self.commands.append((command, timeout))
        result = self.execute_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def disconnect(self) -> None:
        self.events.append("ssh_disconnect")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manager", "expected"),
    (
        (
            _Manager(execute_results=[(True, "16\n", "")]),
            CPUCountResult(True, 16, "CPU count retrieved successfully"),
        ),
        (
            _Manager(
                execute_results=[
                    (True, "unknown", ""),
                    (True, "8\n", ""),
                ]
            ),
            CPUCountResult(True, 8, "CPU count retrieved successfully"),
        ),
        (
            _Manager(connect_result=(False, "offline")),
            CPUCountResult(False, 32, "Failed to connect: offline"),
        ),
        (
            _Manager(
                execute_results=[
                    (False, "", "failed"),
                    (False, "", "failed"),
                ]
            ),
            CPUCountResult(False, 32, "Failed to detect CPU count, using default"),
        ),
        (
            _Manager(execute_results=[RuntimeError("channel closed")]),
            CPUCountResult(False, 32, "Error: channel closed"),
        ),
    ),
)
async def test_cpu_service_preserves_fallbacks_and_always_disconnects(
    manager: _Manager,
    expected: CPUCountResult,
) -> None:
    result = await CPUCountService(manager).get_cpu_count(_target())

    assert result == expected
    assert manager.events[-1] == "ssh_disconnect"


class _Cache:
    def __init__(
        self,
        events: list[str] | None = None,
        *,
        value: object = None,
        get_error: Exception | None = None,
        set_error: Exception | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.value = value
        self.get_error = get_error
        self.set_error = set_error
        self.set_calls: list[tuple[str, object, int]] = []

    async def get(self, key: str) -> object:
        self.events.append("cache_get")
        if self.get_error is not None:
            raise self.get_error
        return self.value

    async def set(self, key: str, value: object, expire: int = 300) -> bool:
        self.events.append("cache_set")
        if self.set_error is not None:
            raise self.set_error
        self.set_calls.append((key, value, expire))
        return True


@pytest.mark.asyncio
async def test_disk_service_returns_valid_application_cache_without_ssh() -> None:
    manager = _Manager()
    cache = _Cache(
        value={
            "used_gb": 1.5,
            "total_gb": 10,
            "available_gb": 8,
            "used_percent": 15,
        }
    )

    result = await DiskSpaceService(cache, manager).get_disk_space(_target())

    assert result.success is True
    assert result.disk_space == DiskSpaceInfo(
        used_gb=1.5,
        total_gb=10,
        available_gb=8,
        used_percent=15,
    )
    assert manager.events == []


@pytest.mark.asyncio
async def test_disk_service_reads_live_data_quotes_path_and_caches() -> None:
    events: list[str] = []
    manager = _Manager(
        events,
        execute_results=[
            (True, str(2 * 1024**3), ""),
            (True, "/dev/sda 10G 2G 8G 20% /srv", ""),
        ],
    )
    cache = _Cache(events)

    result = await DiskSpaceService(cache, manager).get_disk_space(
        _target(),
        force_refresh=True,
    )

    assert result.disk_space == DiskSpaceInfo(
        used_gb=2,
        total_gb=10,
        available_gb=8,
        used_percent=20,
    )
    assert manager.commands == [
        (
            "du -sb '/srv/cs2 folder' 2>/dev/null | awk '{print $1}' || echo '0'",
            60,
        ),
        ("df -BG '/srv/cs2 folder' | tail -1", 30),
    ]
    assert cache.set_calls == [
        (
            "disk_space:17",
            {
                "used_gb": 2.0,
                "total_gb": 10.0,
                "available_gb": 8.0,
                "used_percent": 20.0,
            },
            3600,
        )
    ]
    assert events == [
        "ssh_connect",
        "ssh_execute",
        "ssh_execute",
        "ssh_disconnect",
        "cache_set",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "manager",
    (
        _Manager(connect_result=(False, "offline")),
        _Manager(execute_results=[(False, "", "du failed")]),
        _Manager(execute_results=[(True, "not-an-int", "")]),
        _Manager(
            execute_results=[
                (True, "0", ""),
                (False, "", "df failed"),
            ]
        ),
        _Manager(
            execute_results=[
                (True, "0", ""),
                (True, "invalid", ""),
            ]
        ),
    ),
)
async def test_disk_service_returns_legacy_failure_shape_for_remote_failures(
    manager: _Manager,
) -> None:
    result = await DiskSpaceService(_Cache(value=[]), manager).get_disk_space(_target())

    assert result == DiskSpaceResult(
        success=False,
        server_directory="/srv/cs2 folder",
        message="Failed to retrieve disk space information",
    )
    assert manager.events[-1] == "ssh_disconnect"


@pytest.mark.asyncio
async def test_disk_service_tolerates_cache_read_and_write_failures() -> None:
    events: list[str] = []
    manager = _Manager(
        events,
        execute_results=[
            (True, "0", ""),
            (True, "/dev/sda 0G 0G 0G 0% /srv", ""),
        ],
    )
    cache = _Cache(
        events,
        get_error=RuntimeError("read unavailable"),
        set_error=RuntimeError("write unavailable"),
    )

    result = await DiskSpaceService(cache, manager).get_disk_space(_target())

    assert result.disk_space is not None
    assert result.disk_space.used_percent == 0
    assert events == [
        "cache_get",
        "ssh_connect",
        "ssh_execute",
        "ssh_execute",
        "ssh_disconnect",
        "cache_set",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manager", "expected"),
    (
        (
            _Manager(connect_result=(False, "offline")),
            DeploymentCheckResult(
                False,
                "/srv/cs2 folder/cs2/game/bin/linuxsteamrt64/cs2",
                "Could not connect to server: offline",
                True,
            ),
        ),
        (
            _Manager(execute_results=[(True, "exists\n", "")]),
            DeploymentCheckResult(
                True,
                "/srv/cs2 folder/cs2/game/bin/linuxsteamrt64/cs2",
                "Server is deployed",
                False,
            ),
        ),
        (
            _Manager(execute_results=[(True, "missing\n", "")]),
            DeploymentCheckResult(
                False,
                "/srv/cs2 folder/cs2/game/bin/linuxsteamrt64/cs2",
                "Server is not deployed",
                False,
            ),
        ),
        (
            _Manager(connect_error=RuntimeError("host key mismatch")),
            DeploymentCheckResult(
                False,
                "/srv/cs2 folder/cs2/game/bin/linuxsteamrt64/cs2",
                "Error checking deployment: host key mismatch",
                True,
            ),
        ),
    ),
)
async def test_deployment_service_reports_remote_outcomes_and_disconnects(
    manager: _Manager,
    expected: DeploymentCheckResult,
) -> None:
    result = await DeploymentCheckService(manager).check(_target())

    assert result == expected
    assert manager.events[-1] == "ssh_disconnect"
    if manager.commands:
        assert manager.commands[0][0] == (
            "test -f '/srv/cs2 folder/cs2/game/bin/linuxsteamrt64/cs2' "
            "&& echo 'exists' || echo 'missing'"
        )


def _request(
    *,
    cache: object,
    pool: object | None = object(),
    database: object | None = None,
) -> Request:
    http_resource = SimpleNamespace(
        is_closed=False,
        get=lambda *_args, **_kwargs: None,
        post=lambda *_args, **_kwargs: None,
        borrow_client=lambda: None,
        download_file=lambda *_args, **_kwargs: None,
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            container=SimpleNamespace(
                redis=cache,
                ssh_pool=pool,
                http=http_resource,
                database=database,
            )
        )
    )
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_routes_commit_uow_before_cache_and_ssh_io() -> None:
    events: list[str] = []
    cache = _Cache(events)
    session = _Session(_server(), events)
    cpu_manager = _Manager(events, execute_results=[(True, "12", "")])

    async with UnitOfWork(lambda: session) as uow:  # type: ignore[arg-type]
        cpu_response = await maintenance.get_server_cpu_count(
            17,
            ssh_manager=cpu_manager,  # type: ignore[arg-type]
            uow=uow,
            current_user=_principal(),
        )

    assert isinstance(cpu_response, CPUCountResponse)
    assert events[:3] == ["db_query", "db_commit", "ssh_connect"]
    assert events[-1] == "db_close"

    events.clear()
    disk_manager = _Manager(
        events,
        execute_results=[
            (True, "0", ""),
            (True, "/dev/sda 10G 2G 8G 20% /srv", ""),
        ],
    )
    session = _Session(_server(), events)
    async with UnitOfWork(lambda: session) as uow:  # type: ignore[arg-type]
        disk_response = await maintenance.get_server_disk_space(
            _request(cache=cache),
            17,
            ssh_manager=disk_manager,  # type: ignore[arg-type]
            force_refresh=False,
            uow=uow,
            current_user=_principal(),
        )

    assert isinstance(disk_response, DiskSpaceSuccessResponse)
    assert events[:4] == ["db_query", "db_commit", "cache_get", "ssh_connect"]

    events.clear()
    deployment_manager = _Manager(events, execute_results=[(True, "exists", "")])
    session = _Session(_server(), events)
    async with UnitOfWork(lambda: session) as uow:  # type: ignore[arg-type]
        deployment_response = await maintenance.check_server_deployment(
            17,
            ssh_manager=deployment_manager,  # type: ignore[arg-type]
            uow=uow,
            current_user=_principal(),
        )

    assert isinstance(deployment_response, DeploymentCheckResponse)
    assert events[:3] == ["db_query", "db_commit", "ssh_connect"]


@pytest.mark.asyncio
async def test_route_maps_domain_404_without_remote_io() -> None:
    events: list[str] = []
    session = _Session(None, events)
    async with UnitOfWork(lambda: session) as uow:  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as exc_info:
            await maintenance.get_server_cpu_count(
                404,
                ssh_manager=_Manager(events),  # type: ignore[arg-type]
                uow=uow,
                current_user=_principal(),
            )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Server not found"
    assert "db_commit" not in events
    assert "ssh_connect" not in events
    assert "db_rollback" in events


@pytest.mark.asyncio
async def test_disk_route_fails_closed_without_application_cache() -> None:
    session = _Session(_server())
    async with UnitOfWork(lambda: session) as uow:  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as exc_info:
            await maintenance.get_server_disk_space(
                _request(cache=None),
                17,
                ssh_manager=_Manager(),  # type: ignore[arg-type]
                uow=uow,
                current_user=_principal(),
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "System information cache is unavailable"
    assert session.statements == []


def test_app_ssh_provider_isolated_and_fails_closed_without_pool() -> None:
    first_pool = SimpleNamespace(name="first")
    second_pool = SimpleNamespace(name="second")
    first = get_ssh_manager(_request(cache=_Cache(), pool=first_pool))
    second = get_ssh_manager(_request(cache=_Cache(), pool=second_pool))

    assert first.connection_pool is first_pool
    assert second.connection_pool is second_pool
    with pytest.raises(HTTPException) as exc_info:
        get_ssh_manager(_request(cache=_Cache(), pool=None))
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "SSH connection pool is unavailable"


def test_inactive_uow_and_missing_cache_adapter_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="not active"):
        maintenance._uow_session(UnitOfWork(lambda: _Session(_server())))

    with pytest.raises(HTTPException) as exc_info:
        maintenance._require_system_info_cache(_request(cache=SimpleNamespace(get=lambda: None)))
    assert exc_info.value.status_code == 503


def test_response_mappers_keep_success_and_failure_wire_shapes() -> None:
    assert cpu_count_response(
        CPUCountResult(True, 4, "CPU count retrieved successfully")
    ) == CPUCountResponse(
        success=True,
        cpu_count=4,
        message="CPU count retrieved successfully",
    )
    assert isinstance(
        disk_space_response(
            DiskSpaceResult(
                True,
                "/srv/cs2",
                DiskSpaceInfo(
                    used_gb=1,
                    total_gb=2,
                    available_gb=1,
                    used_percent=50,
                ),
            )
        ),
        DiskSpaceSuccessResponse,
    )
    assert disk_space_response(DiskSpaceResult(False, "/srv/cs2")) == DiskSpaceFailureResponse(
        success=False,
        message="Failed to retrieve disk space information",
        server_directory="/srv/cs2",
    )
    deployment = DeploymentCheckResult(False, "/binary", "missing", False)
    assert deployment_check_response(deployment) == DeploymentCheckResponse(
        is_deployed=False,
        binary_path="/binary",
        message="missing",
        error=False,
    )


def test_routes_declare_principal_uow_ssh_and_precise_response_contracts() -> None:
    expected = {
        "/servers/{server_id}/cpu-count": CPUCountResponse,
        "/servers/{server_id}/disk-space": DiskSpaceResponse,
        "/servers/{server_id}/check-deployment": DeploymentCheckResponse,
    }
    for route in maintenance.router.routes:
        if route.path not in expected:
            continue
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert get_ssh_manager in dependency_calls
        assert maintenance.get_unit_of_work in dependency_calls
        assert maintenance.get_current_principal in dependency_calls
        assert route.response_model == expected[route.path]
        assert route.status_code == 200
        assert set(route.responses) == {401, 404, 503}
