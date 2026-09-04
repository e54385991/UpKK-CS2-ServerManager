"""覆盖主机依赖探测器的 SSH runner 和重试边界。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import host_initialization as host


class _Conn:
    def __init__(self, results=()):
        self.results = list(results)
        self.commands = []

    async def run(self, command, **_kwargs):
        self.commands.append(command)
        result = self.results.pop(0) if self.results else SimpleNamespace(exit_status=0)
        if isinstance(result, BaseException):
            raise result
        return result


class _Manager:
    def __init__(self, sudo_results=(), command_results=()):
        self.sudo_results = list(sudo_results)
        self.command_results = list(command_results)
        self.sudo_calls = []
        self.commands = []

    async def execute_command(self, command, timeout=60):
        self.commands.append((command, timeout))
        value = self.command_results.pop(0) if self.command_results else (True, "", "")
        return value

    async def execute_sudo_command(self, command, password, timeout=600):
        self.sudo_calls.append((command, password, timeout))
        return self.sudo_results.pop(0) if self.sudo_results else (True, "", "")


def _result(stdout="", stderr="", status=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=status)


@pytest.mark.asyncio
async def test_asyncssh_runner_privilege_modes_and_command_normalization():
    conn = _Conn([_result("out\n", "err\n", 2)])
    runner = host.AsyncsshHostRunner(conn, sudo_password="pw")
    assert await runner.run("echo x") == (2, "out\n", "err\n")

    root = host.AsyncsshHostRunner(_Conn([_result("root"), _result()]), sudo_password="pw")
    assert await root.resolve_privilege() == "root"
    assert await root.run_privileged("id") == (0, "", "")

    nopass = host.AsyncsshHostRunner(
        _Conn([_result("steam"), _result(status=0)]), sudo_password="pw"
    )
    assert await nopass.resolve_privilege() == "sudo"
    assert nopass._sudo_password is None

    password = host.AsyncsshHostRunner(
        _Conn([_result("steam"), _result(status=1), _result(status=0)]), sudo_password="pw"
    )
    assert await password.resolve_privilege() == "sudo"
    assert "sudo -S" in password._conn.commands[-1]

    none = host.AsyncsshHostRunner(
        _Conn([_result("steam"), _result(status=1), _result(status=1)]), sudo_password=""
    )
    assert await none.resolve_privilege() == "none"
    no_pw = host.AsyncsshHostRunner(_Conn([_result("steam", status=1)]))
    assert await no_pw.run_privileged("id") == (1, "steam", "")


@pytest.mark.asyncio
async def test_manager_runner_privilege_fallbacks_and_progress_helpers(monkeypatch):
    manager = _Manager(command_results=[(True, "stdout", "stderr")])
    runner = host.SshManagerHostRunner(manager, SimpleNamespace(ssh_password="pw"))
    assert await runner.run("id", timeout=3) == (0, "stdout", "stderr")
    assert await runner.run_privileged("id") == (0, "", "")
    assert manager.sudo_calls[0][1] == "pw"

    fallback_manager = _Manager(sudo_results=[(False, "out", "bad"), (True, "ok", "")])
    fallback = host.SshManagerHostRunner(fallback_manager, SimpleNamespace(ssh_password="pw"))
    assert await fallback.run_privileged("apt") == (0, "ok", "")
    assert fallback._sudo_password is None
    failed_manager = _Manager(sudo_results=[(False, "out", "bad"), (False, "", "still bad")])
    failed = host.SshManagerHostRunner(failed_manager, SimpleNamespace(ssh_password="pw"))
    assert await failed.run_privileged("apt") == (1, "out", "bad")

    root_manager = _Manager(command_results=[(True, "root", "")])
    root = host.SshManagerHostRunner(root_manager, SimpleNamespace(sudo_password="pw"))
    assert await root.resolve_privilege() == "root"
    root._privilege = "root"
    assert await root.run_privileged("id") == (0, "", "")

    nopass = host.SshManagerHostRunner(
        _Manager(command_results=[(True, "steam", ""), (True, "", "")]),
        SimpleNamespace(ssh_password="pw"),
    )
    assert await nopass.resolve_privilege() == "sudo"
    none = host.SshManagerHostRunner(
        _Manager(command_results=[(True, "steam", ""), (False, "", "")]),
        SimpleNamespace(ssh_password=None),
    )
    assert await none.resolve_privilege() == "none"

    logs = []
    await host._emit(None, logs, "message")
    await host._emit(lambda _message: None, logs, "sync")
    async_progress = AsyncMock()
    await host._emit(async_progress, logs, "async")
    assert logs == ["message", "sync", "async"]
    async_progress.assert_awaited_once_with("async")

    assert host._manual_message([], reason="bad").startswith("bad")
    failed = host._failed(
        architecture="amd64",
        architecture_supported=True,
        missing_before=("a",),
        missing_after=(),
        installed=False,
        privilege="none",
        reason="no privilege",
        logs=("x",),
    )
    assert not failed.success and failed.manual_install_command
    value = SimpleNamespace()
    host.attach_host_initialization(value, failed)
    assert host.host_initialization_of(value) is failed
    assert host.host_initialization_of(SimpleNamespace()) is None


@pytest.mark.asyncio
async def test_retry_probe_os_release_and_install_result_boundaries(monkeypatch):
    class _Runner:
        def __init__(self, results):
            self.results = list(results)
            self.calls = []

        async def run(self, command, **_kwargs):
            self.calls.append(command)
            return self.results.pop(0)

        async def run_privileged(self, command, **_kwargs):
            self.calls.append(command)
            value = self.results.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value

        async def resolve_privilege(self):
            return "sudo"

    assert await host._probe_missing(_Runner([(0, "", "")]), ["a"]) == []
    assert await host._probe_missing(_Runner([(1, "", "")]), ["a"]) == ["a"]
    assert await host._detect_os_release(_Runner([(1, "", "")])) is None
    assert await host._detect_os_release(
        _Runner([(0, "ID=ubuntu\nVERSION_ID=22.04\nVERSION_CODENAME=jammy\n", "")])
    ) is not None

    timeout_runner = _Runner([TimeoutError(), (1, "", "failed"), (1, "", "failed")])
    monkeypatch.setattr(host.asyncio, "sleep", AsyncMock())
    code, _out, _err = await host._run_privileged_with_retry(
        timeout_runner, "apt", description="apt", progress=None, logs=[]
    )
    assert code == 1

    failure = await host._finish_package_install(
        _Runner([(1, "", "missing")]),
        ["a"],
        ["a"],
        architecture="amd64",
        privilege="sudo",
        active_mirror=None,
        failed_mirrors=[],
        logs=[],
        progress=None,
    )
    assert not failure.success
