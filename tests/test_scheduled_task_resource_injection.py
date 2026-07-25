"""Scheduled background work must use constructor-owned resources."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules.http_helper import http_helper
from modules.models import AuthType, ScheduledTask, Server, User
from services.maintenance_lock import maintenance_lock_service
from services.s3_backup_service import s3_backup_service
from services.scheduled_task_service import ScheduledTaskService


def _server() -> Server:
    return Server(
        id=31,
        user_id=7,
        name="scheduled-target",
        host="server.example.com",
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
    )


def _task(action: str = "restart") -> ScheduledTask:
    return ScheduledTask(
        id=41,
        server_id=31,
        name="nightly",
        action=action,
        enabled=True,
        schedule_type="daily",
        schedule_value="04:00",
    )


class _Session:
    def __init__(self, values: dict[type, object]) -> None:
        self.values = values
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.closed = True

    async def get(self, model, _object_id):
        return self.values.get(model)


class _SessionFactory:
    def __init__(self, values: dict[type, object]) -> None:
        self.values = values
        self.sessions: list[_Session] = []

    def __call__(self):
        session = _Session(self.values)
        self.sessions.append(session)
        return session


class _Lease:
    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    async def acquire(self) -> None:
        self.acquired += 1

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        self.released += 1


@pytest.mark.asyncio
async def test_scheduled_execution_uses_injected_db_lock_and_ssh_snapshot(monkeypatch) -> None:
    server = _server()
    session_factory = _SessionFactory({Server: server})
    lease = _Lease()
    lock_service = SimpleNamespace(get=Mock(return_value=lease))

    class Manager:
        def __init__(self) -> None:
            self.connected_server = None
            self.disconnect = AsyncMock()

        async def connect(self, detached_server):
            assert session_factory.sessions[0].closed is True
            assert detached_server is not server
            self.connected_server = detached_server
            return True, "connected"

    manager = Manager()
    service = ScheduledTaskService(
        session_factory=session_factory,  # type: ignore[arg-type]
        lock_service=lock_service,  # type: ignore[arg-type]
        s3_service=SimpleNamespace(),  # type: ignore[arg-type]
        ssh_manager_factory=lambda: manager,  # type: ignore[arg-type]
    )
    execute_action = AsyncMock(return_value=(True, "done"))
    update_status = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(service, "_execute_action", execute_action)
    monkeypatch.setattr(service, "_update_task_status", update_status)
    monkeypatch.setattr(service, "_notify_task_result", notify)

    await service._execute_task(_task())

    lock_service.get.assert_called_once()
    assert lock_service.get.call_args.args == (31,)
    assert lease.acquired == lease.released == 1
    assert manager.connected_server.id == 31
    execute_action.assert_awaited_once()
    update_status.assert_awaited_once_with(41, "success", None)
    notify.assert_awaited_once()
    manager.disconnect.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_scheduled_execution_notifies_with_snapshot_after_transport_error(
    monkeypatch,
) -> None:
    server = _server()
    session_factory = _SessionFactory({Server: server})
    lease = _Lease()
    lock_service = SimpleNamespace(get=Mock(return_value=lease))

    class Manager:
        async def connect(self, detached_server):
            assert session_factory.sessions[0].closed is True
            assert detached_server is not server
            raise RuntimeError("transport unavailable")

    service = ScheduledTaskService(
        session_factory=session_factory,  # type: ignore[arg-type]
        lock_service=lock_service,  # type: ignore[arg-type]
        s3_service=SimpleNamespace(),  # type: ignore[arg-type]
        ssh_manager_factory=Manager,  # type: ignore[arg-type]
    )
    update_status = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(service, "_update_task_status", update_status)
    monkeypatch.setattr(service, "_notify_task_result", notify)

    await service._execute_task(_task())

    notified_server = notify.await_args.args[0]
    assert notified_server is not server
    assert notified_server.id == server.id
    update_status.assert_awaited_once_with(41, "failed", "transport unavailable")
    notify.assert_awaited_once_with(
        notified_server,
        notify.await_args.args[1],
        False,
        "transport unavailable",
    )
    assert lease.acquired == lease.released == 1


@pytest.mark.asyncio
async def test_scheduled_s3_backup_uses_injected_service_after_owner_session_closes(
    monkeypatch,
) -> None:
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
    session_factory = _SessionFactory({User: owner})

    class Manager:
        last_plugin_backup = {"path": "/remote/plugins.tar.gz"}

        async def backup_plugins(self, _server, progress_callback=None):
            del progress_callback
            return True, "local backup complete"

    manager = Manager()

    async def upload(_manager, server, configuration, backup_path, **_kwargs):
        assert session_factory.sessions[0].closed is True
        assert configuration is not owner
        assert configuration.id == owner.id
        assert server.id == 31
        assert backup_path == "/remote/plugins.tar.gz"
        return True, "S3 upload complete", "user-7/server-31/plugins.tar.gz"

    injected_s3 = SimpleNamespace(
        is_configured=Mock(return_value=True),
        upload_remote_backup=AsyncMock(side_effect=upload),
    )
    service = ScheduledTaskService(
        session_factory=session_factory,  # type: ignore[arg-type]
        lock_service=SimpleNamespace(),  # type: ignore[arg-type]
        s3_service=injected_s3,  # type: ignore[arg-type]
        ssh_manager_factory=lambda: manager,  # type: ignore[arg-type]
    )
    global_upload = AsyncMock(side_effect=AssertionError("global S3 must not be used"))
    monkeypatch.setattr(s3_backup_service, "upload_remote_backup", global_upload)

    success, message = await service._execute_action(manager, _server(), "backup_plugins")

    assert success is True
    assert message == "local backup complete\nS3 upload complete"
    injected_s3.upload_remote_backup.assert_awaited_once()
    global_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_s3_backup_accepts_lifespan_owned_runtime_service(
    monkeypatch,
) -> None:
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
    session_factory = _SessionFactory({User: owner})
    manager = SimpleNamespace(
        last_plugin_backup={"path": "/remote/plugins.tar.gz"},
        backup_plugins=AsyncMock(return_value=(True, "local backup complete")),
    )
    constructor_s3 = SimpleNamespace(
        is_configured=Mock(side_effect=AssertionError("constructor S3 must not be used")),
    )
    runtime_s3 = SimpleNamespace(
        is_configured=Mock(return_value=True),
        upload_remote_backup=AsyncMock(return_value=(True, "uploaded", "object-key")),
    )
    service = ScheduledTaskService(
        session_factory=session_factory,  # type: ignore[arg-type]
        s3_service=constructor_s3,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        s3_backup_service,
        "upload_remote_backup",
        AsyncMock(side_effect=AssertionError("global S3 must not be used")),
    )

    success, message = await service._execute_action(
        manager,  # type: ignore[arg-type]
        _server(),
        "backup_plugins",
        s3_service=runtime_s3,  # type: ignore[arg-type]
    )

    assert success is True
    assert message == "local backup complete\nuploaded"
    runtime_s3.is_configured.assert_called_once()
    runtime_s3.upload_remote_backup.assert_awaited_once()
    constructor_s3.is_configured.assert_not_called()


@pytest.mark.asyncio
async def test_scheduled_map_sync_uses_injected_http_resource(monkeypatch) -> None:
    server = _server()
    server.map_pool_sync_url = "https://maps.example.com/maps.txt"
    manager = SimpleNamespace()
    outbound_http = SimpleNamespace(name="application-http")

    async def synchronize(
        received_manager,
        received_server,
        url: str,
        *,
        http_resource: object,
    ):
        assert received_manager is manager
        assert received_server is server
        assert url == server.map_pool_sync_url
        assert http_resource is outbound_http
        return '"Maplist"\n{\n}\n', 12

    monkeypatch.setattr(
        "services.remote_map_pool_service.synchronize_remote_map_pool",
        synchronize,
    )
    service = ScheduledTaskService(
        http_resource=outbound_http,
    )

    success, message = await service._execute_action(
        manager,  # type: ignore[arg-type]
        server,
        "map_pool_sync",
    )

    assert success is True
    assert message == "Synchronized 12 maps from the custom remote map pool"


def test_scheduled_service_legacy_facade_keeps_explicit_compatibility_defaults() -> None:
    service = ScheduledTaskService()

    assert service._lock_service is maintenance_lock_service
    assert service._s3_service is s3_backup_service
    assert service._http_resource is http_helper
    assert callable(service._session_factory)
    assert callable(service._ssh_manager_factory)
