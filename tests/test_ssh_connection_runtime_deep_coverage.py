"""覆盖 SSH 连接运行时的鉴权、重连、流式输出和断开分支。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest

from services.ssh_manager import SSHManager


def _server(**overrides):
    values = dict(
        id=8,
        host="example.test",
        ssh_port=22,
        ssh_user="steam",
        ssh_password="pw",
        ssh_key_path="/tmp/key",
        is_password_auth=True,
        is_key_auth=False,
        auth_type="password",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class _RunConnection:
    def __init__(self, *results):
        self.results = list(results)
        self.process_calls = []
        self.closed = False

    async def run(self, *_args, **_kwargs):
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def create_process(self, *args, **kwargs):
        self.process_calls.append((args, kwargs))
        return SimpleNamespace(
            stdout=_Stream(b"out\n"),
            stderr=_Stream(b"err\r"),
            wait=AsyncMock(return_value=SimpleNamespace(exit_status=0)),
        )

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class _Stream:
    def __init__(self, *chunks):
        self.chunks = list(chunks)

    async def read(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


def _completed(status=0, stdout=b"out", stderr=b"err"):
    return SimpleNamespace(exit_status=status, stdout=stdout, stderr=stderr)


@pytest.mark.asyncio
async def test_connect_pool_and_direct_failure_matrix(monkeypatch):
    from services.ssh import connection_runtime

    status_calls = []
    monkeypatch.setattr(
        connection_runtime,
        "_schedule_legacy_status_update",
        lambda *args: status_calls.append(args),
    )
    pooled = SimpleNamespace(
        get_connection=AsyncMock(return_value=(True, "pooled", "pool ok")),
        release_connection=AsyncMock(),
    )
    monkeypatch.setattr(
        connection_runtime,
        "_legacy_connection_module",
        lambda: SimpleNamespace(ssh_connection_pool=pooled),
    )
    manager = SSHManager(use_pool=True)
    assert await manager.connect(_server()) == (True, "pool ok")
    assert manager.conn == "pooled" and status_calls[-1] == (8, True)

    asyncssh_connect = AsyncMock()
    monkeypatch.setattr(connection_runtime.asyncssh, "connect", asyncssh_connect)
    cases = [
        (asyncssh.PermissionDenied("denied"), "Authentication failed"),
        (asyncio.TimeoutError(), "SSH connection timeout"),
        (asyncssh.Error(1, "ssh failed"), "SSH error: ssh failed"),
        (RuntimeError("broken"), "Connection error: broken"),
    ]
    for exception, expected in cases:
        asyncssh_connect.side_effect = exception
        direct = SSHManager(use_pool=False)
        ok, message = await direct.connect(_server())
        assert not ok and expected in message
    unsupported = SSHManager(use_pool=False)
    assert await unsupported.connect(_server(is_password_auth=False, is_key_auth=False)) == (
        False,
        "Unsupported auth type: password",
    )


@pytest.mark.asyncio
async def test_execute_command_reconnect_and_interactive_fallback(monkeypatch):

    manager = SSHManager(use_pool=False)
    manager.conn = _RunConnection(_completed(1, b"no", b"bad"))
    assert await manager.execute_command("false") == (False, "no", "bad")
    manager.conn = _RunConnection(RuntimeError("command failed"))
    assert await manager.execute_command("bad") == (False, "", "command failed")

    old = _RunConnection(asyncssh.ConnectionLost("lost"), _completed(0, b"retry", b""))
    manager = SSHManager(use_pool=True)
    manager.conn = old
    manager.current_server = _server()
    manager._reconnect_current_pooled_connection = AsyncMock(
        return_value=(True, old, "reconnected")
    )
    assert await manager.execute_command("retry") == (True, "retry", "")

    manager._reconnect_current_pooled_connection = AsyncMock(
        return_value=(False, None, "still offline")
    )
    manager.conn = _RunConnection(asyncssh.ConnectionLost("lost"))
    assert "Connection failed" in (await manager.execute_command("retry"))[2]
    manager._reconnect_current_pooled_connection = AsyncMock(side_effect=RuntimeError("again"))
    manager.conn = _RunConnection(asyncssh.ConnectionLost("lost"))
    assert "after reconnection" in (await manager.execute_command("retry"))[2]

    fallback = _RunConnection()
    first = True

    async def process_with_fallback(*args, **kwargs):
        nonlocal first
        if first and kwargs.get("encoding") is None and kwargs.get("term_type"):
            first = False
            raise TypeError("encoding unsupported")
        return SimpleNamespace()

    fallback.create_process = process_with_fallback
    manager = SSHManager(use_pool=False)
    manager.conn = fallback
    assert await manager.create_interactive_process("tmux") is not None
    assert manager.conn is fallback


@pytest.mark.asyncio
async def test_streaming_output_callbacks_timeout_and_reconnect(monkeypatch):
    from services.ssh import connection_runtime

    manager = SSHManager(use_pool=False)
    manager.conn = _RunConnection()
    sync_lines = []
    async_lines = []

    def sync_callback(line):
        sync_lines.append(line)

    ok, stdout, stderr = await manager.execute_command_streaming("echo", sync_callback)
    assert ok and stdout == "out" and stderr == "err"
    assert sync_lines == ["out", "[STDERR] err"]

    manager.conn = _RunConnection()

    async def async_callback(line):
        async_lines.append(line)

    ok, _, _ = await manager.execute_command_streaming("echo", async_callback)
    assert ok and async_lines

    class BrokenStream:
        async def read(self, _size):
            raise RuntimeError("stream broken")

    broken = _RunConnection()
    broken.create_process = AsyncMock(
        return_value=SimpleNamespace(
            stdout=BrokenStream(),
            stderr=_Stream(b"err\n"),
            wait=AsyncMock(return_value=SimpleNamespace(exit_status=1)),
        )
    )
    manager.conn = broken
    ok, stdout, stderr = await manager.execute_command_streaming("echo")
    assert not ok and "err" in stderr and stdout == ""

    manager.conn = _RunConnection()

    async def timeout_wait(awaitable, _timeout=None, **_kwargs):
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(connection_runtime.asyncio, "wait_for", timeout_wait)
    ok, _, error = await manager.execute_command_streaming("slow")
    assert (ok, error) == (False, "Command timeout")

    lines = connection_runtime.BoundedLineBuffer(100)
    retry_manager = SSHManager(use_pool=True)
    retry_manager.current_server = _server()
    retry_manager._reconnect_current_pooled_connection = AsyncMock(
        return_value=(False, None, "offline")
    )
    result = await retry_manager._retry_stream_after_error(
        asyncssh.ConnectionLost("lost"),
        lines,
        connection_runtime.BoundedLineBuffer(100),
        AsyncMock(),
        1,
    )
    assert "Connection failed" in result[2]


@pytest.mark.asyncio
async def test_streaming_connection_error_retries_and_sudo_paths(monkeypatch):
    manager = SSHManager(use_pool=True)
    manager.current_server = _server()
    old = _RunConnection(asyncssh.ConnectionLost("lost"))
    old.create_process = AsyncMock(side_effect=asyncssh.ConnectionLost("lost"))
    manager.conn = old
    manager._reconnect_current_pooled_connection = AsyncMock(side_effect=RuntimeError("retry bad"))
    result = await manager.execute_command_streaming("echo")
    assert "after reconnection" in result[2]

    manager = SSHManager(use_pool=False)
    manager.conn = _RunConnection(_completed(0, b"ok", b""))
    assert await manager.execute_sudo_command("id") == (True, "ok", "")
    manager.conn = _RunConnection(_completed(1, b"", b"denied"))
    assert await manager.execute_sudo_command("id", "pw") == (False, "", "denied")
    manager.conn = _RunConnection(asyncio.TimeoutError())
    assert await manager.execute_sudo_command("slow") == (False, "", "Command timeout")
    manager.conn = _RunConnection(RuntimeError("sudo failed"))
    assert await manager.execute_sudo_command("bad") == (False, "", "sudo failed")


@pytest.mark.asyncio
async def test_disconnect_pool_and_no_connection_sudo(monkeypatch):
    from services.ssh import connection_runtime

    manager = SSHManager(use_pool=False)
    assert await manager.disconnect() is None
    assert await manager.execute_sudo_command("id") == (False, "", "Not connected")

    pool = SimpleNamespace(release_connection=AsyncMock())
    monkeypatch.setattr(
        connection_runtime,
        "_legacy_connection_module",
        lambda: SimpleNamespace(ssh_connection_pool=pool),
    )
    manager = SSHManager(use_pool=True)
    server = _server()
    manager.current_server = server
    manager.conn = "leased"
    await manager.disconnect()
    pool.release_connection.assert_awaited_once_with(server, "leased")
    assert manager.conn is None and manager.current_server is None
