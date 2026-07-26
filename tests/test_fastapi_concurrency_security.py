import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from pydantic import ValidationError

import main
from modules import auth
from modules.models import AuthType
from modules.schemas import BatchActionRequest
from services.a2s_cache_service import A2SCacheService
from services.maintenance_lock import MaintenanceLockService, OperationBusyError
from services.map_management_service import DEFAULT_MAPS_CONFIG
from services.rate_limit import enforce_rate_limit
from services.redis_manager import redis_manager
from services.scheduled_task_service import scheduled_task_service
from services.ssh_connection_pool import SSHConnectionPool


class _FakeSSHConnection:
    def __init__(self):
        self.closed = False

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def _server(host: str, server_id: int):
    return SimpleNamespace(
        id=server_id,
        host=host,
        ssh_port=22,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        is_password_auth=True,
        is_key_auth=False,
        ssh_password="test",
        ssh_key_path=None,
        is_ssh_down=False,
    )


@pytest.mark.asyncio
async def test_slow_ssh_target_does_not_hold_pool_wide_lock(monkeypatch):
    previous_instance = SSHConnectionPool._instance
    SSHConnectionPool._instance = None
    pool = SSHConnectionPool()
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()

    async def open_connection(server):
        if server.host == "slow.example":
            slow_started.set()
            await release_slow.wait()
        return _FakeSSHConnection()

    monkeypatch.setattr(pool, "_open_connection", open_connection)
    try:
        slow_task = asyncio.create_task(pool.get_connection(_server("slow.example", 1)))
        await slow_started.wait()
        fast_result = await asyncio.wait_for(
            pool.get_connection(_server("fast.example", 2)),
            timeout=0.2,
        )
        assert fast_result[0] is True
        release_slow.set()
        assert (await slow_task)[0] is True
    finally:
        release_slow.set()
        await pool.close_all()
        SSHConnectionPool._instance = previous_instance


@pytest.mark.asyncio
async def test_bcrypt_wrapper_keeps_event_loop_responsive(monkeypatch):
    import time

    def slow_verify(*args):
        time.sleep(0.15)
        return True

    monkeypatch.setattr(auth, "verify_password", slow_verify)
    task = asyncio.create_task(auth.verify_password_async("password", "hash"))
    await asyncio.sleep(0.02)
    assert task.done() is False
    assert await task is True


@pytest.mark.asyncio
async def test_websocket_without_session_is_rejected_before_accept():
    class FakeWebSocket:
        headers = {"host": "panel.example", "origin": "https://panel.example"}
        cookies = {}

        def __init__(self):
            self.closed = None

        async def close(self, code, reason):
            self.closed = (code, reason)

    websocket = FakeWebSocket()
    assert await auth.authenticate_websocket(websocket, 1) == (None, None)
    assert websocket.closed[0] == 4401


@pytest.mark.asyncio
async def test_nonblocking_server_lock_reports_conflict(monkeypatch):
    service = MaintenanceLockService()

    async def lock_is_busy(*args, **kwargs):
        return False

    async def lock_is_not_held(*args, **kwargs):
        return False

    monkeypatch.setattr(redis_manager, "acquire_lock", lock_is_busy)
    monkeypatch.setattr(redis_manager, "is_lock_held", lock_is_not_held)
    with pytest.raises(OperationBusyError):
        async with service.get(42, wait=False):
            pass
    assert await service.is_locked(42) is False


@pytest.mark.asyncio
async def test_server_lock_registry_forgets_inactive_server_ids(monkeypatch):
    service = MaintenanceLockService()

    async def redis_unavailable(*args, **kwargs):
        return None

    monkeypatch.setattr(redis_manager, "acquire_lock", redis_unavailable)

    for server_id in range(100):
        async with service.get(server_id):
            assert service._locks[server_id].locked()

    assert len(service._locks) == 0


def test_batch_requests_are_deduplicated_and_bounded():
    request = BatchActionRequest(server_ids=[1, 1, 2], action="restart")
    assert request.server_ids == [1, 2]
    with pytest.raises(ValidationError):
        BatchActionRequest(server_ids=list(range(21)), action="restart")


@pytest.mark.asyncio
async def test_public_rate_limit_returns_retry_after(monkeypatch):
    async def limited(*args, **kwargs):
        return False, 17

    monkeypatch.setattr(redis_manager, "hit_rate_limit", limited)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    with pytest.raises(Exception) as caught:
        await enforce_rate_limit(request, "login", limit=1, window=60)
    assert caught.value.status_code == 429
    assert caught.value.headers["Retry-After"] == "17"


def test_file_editor_uses_sftp_instead_of_shell_command():
    source = inspect.getsource(main.file_editor_popup)
    assert "read_file" in source
    assert "execute_command" not in source


def test_pyjwt_session_tokens_remain_compatible():
    token = auth.create_access_token({"sub": "123"})
    assert auth._decode_user_id(token) == 123


@pytest.mark.asyncio
async def test_lifespan_cleans_up_after_partial_startup_failure(monkeypatch):
    shutdown_called = False

    async def fail_startup():
        raise RuntimeError("startup failed")

    async def record_shutdown():
        nonlocal shutdown_called
        shutdown_called = True

    monkeypatch.setattr(main, "startup_event", fail_startup)
    monkeypatch.setattr(main, "shutdown_event", record_shutdown)

    context = main.lifespan(main.app)
    with pytest.raises(RuntimeError, match="startup failed"):
        await context.__aenter__()
    assert shutdown_called is True


@pytest.mark.asyncio
async def test_scheduled_restart_does_not_start_after_failed_stop():
    class Manager:
        started = False

        async def check_session_manager_available(self, server):
            return True, "ready"

        async def stop_server(self, server):
            return False, "shutdown timed out"

        async def start_server(self, server, progress_callback=None):
            self.started = True
            return True, "started"

    manager = Manager()
    success, message = await scheduled_task_service._execute_action(
        manager,
        SimpleNamespace(id=9),
        "restart",
    )
    assert success is False
    assert "shutdown failed" in message
    assert manager.started is False


@pytest.mark.asyncio
async def test_scheduled_map_pool_sync_uses_configured_url(monkeypatch):
    synchronize = AsyncMock(return_value=(DEFAULT_MAPS_CONFIG, 12))
    monkeypatch.setattr(
        "services.remote_map_pool_service.synchronize_remote_map_pool",
        synchronize,
    )
    server = SimpleNamespace(
        id=9,
        map_pool_sync_url="https://maps.example.com/maps.txt",
    )

    success, message = await scheduled_task_service._execute_action(
        object(),
        server,
        "map_pool_sync",
    )

    assert success is True
    assert "12 maps" in message
    synchronize.assert_awaited_once_with(
        ANY,
        server,
        server.map_pool_sync_url,
    )


@pytest.mark.asyncio
async def test_a2s_startup_schedules_polling_without_waiting(monkeypatch):
    service = A2SCacheService()
    poll_started = asyncio.Event()
    release_poll = asyncio.Event()

    async def slow_poll():
        poll_started.set()
        await release_poll.wait()

    async def no_op_steam_poll():
        return None

    monkeypatch.setattr(service, "_query_all_servers", slow_poll)
    monkeypatch.setattr(service, "_cache_steam_version", no_op_steam_poll)

    await asyncio.wait_for(service.start(), timeout=0.1)
    await asyncio.wait_for(poll_started.wait(), timeout=0.1)
    release_poll.set()
    await service.stop()
