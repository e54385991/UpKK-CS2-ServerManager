"""覆盖自动设置工作流的权限、依赖、文件和失败回滚分支。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import setup_workflow as setup


def _result(stdout="", stderr="", exit_status=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=exit_status)


class _Conn:
    def __init__(self, results=None):
        self.results = dict(results or {})
        self.calls = []

    async def run(self, command, **_kwargs):
        self.calls.append(command)
        value = self.results.get(command, _result())
        if callable(value):
            value = value(command)
        return value


class _Sftp:
    async def put(self, *_args):
        return None


class _SftpContext:
    async def __aenter__(self):
        return _Sftp()

    async def __aexit__(self, *_args):
        return None


def _request(**overrides):
    values = dict(
        name="demo",
        host="example.test",
        ssh_user="steam",
        ssh_password="ssh-pass",
        sudo_password="sudo-pass",
        cs2_username="cs2server",
        cs2_password="cs2-pass",
        save_config=True,
        open_game_ports=True,
    )
    values.update(overrides)
    return setup.ServerSetupRequest(**values)


def _context(conn, **overrides):
    logs = []
    context = setup._SetupContext(
        request=_request(),
        conn=conn,
        add_log=AsyncMock(side_effect=logs.append),
        add_command_output=AsyncMock(side_effect=logs.append),
        cs2_password="cs2-pass",
    )
    for key, value in overrides.items():
        setattr(context, key, value)
    context.logs = logs
    return context


@pytest.mark.asyncio
async def test_detect_host_rejects_bad_sudo_and_accepts_passwordless_fallback(monkeypatch):
    base = {
        "dpkg --print-architecture 2>/dev/null || uname -m": _result("amd64"),
        "command -v apt-get": _result("/usr/bin/apt-get"),
        "lsb_release -rs 2>/dev/null || sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '\"'": _result(
            "22.04"
        ),
        "whoami": _result("steam"),
    }
    monkeypatch.setattr(
        setup, "run_sudo_command", AsyncMock(side_effect=[("", "bad", 1), ("", "", 0)])
    )
    context = _context(_Conn(base), sudo_password="sudo-pass")
    await setup._detect_setup_host(context)
    assert context.sudo_password is None

    monkeypatch.setattr(
        setup, "run_sudo_command", AsyncMock(side_effect=[("", "bad", 1), ("", "still bad", 1)])
    )
    with pytest.raises(HTTPException) as exc_info:
        await setup._detect_setup_host(_context(_Conn(base), sudo_password="sudo-pass"))
    assert exc_info.value.status_code == 403

    no_password = _context(_Conn(base), request=_request(sudo_password=None, ssh_password=""))
    monkeypatch.setattr(setup, "run_sudo_command", AsyncMock(return_value=("", "bad", 1)))
    with pytest.raises(HTTPException, match="sudo requires a password"):
        await setup._detect_setup_host(no_password)


@pytest.mark.asyncio
async def test_install_dependencies_success_optional_and_failure_paths(monkeypatch):
    conn = _Conn({"command -v sudo": _result(exit_status=1)})
    context = _context(conn)
    apt_results = iter(
        [
            ("updated\n", "", 0),
            ("sudo installed\n", "", 0),
            ("packages\n", "", 0),
            ("optional\n", "", 0),
        ]
    )
    monkeypatch.setattr(
        setup,
        "run_apt_command_with_retry",
        AsyncMock(side_effect=lambda *_a, **_k: next(apt_results)),
    )
    conn.results["apt-cache show --no-all-versions p7zip-full >/dev/null 2>&1"] = _result(
        exit_status=0
    )
    await setup._install_setup_dependencies(context)
    assert any("Optional enhancement dependencies installed" in line for line in context.logs)
    assert context.add_command_output.await_count >= 2

    monkeypatch.setattr(
        setup, "run_apt_command_with_retry", AsyncMock(return_value=("", "update bad", 1))
    )
    with pytest.raises(HTTPException) as exc_info:
        await setup._install_setup_dependencies(_context(_Conn()))
    assert exc_info.value.status_code == 502

    monkeypatch.setattr(
        setup,
        "run_apt_command_with_retry",
        AsyncMock(side_effect=[("", "", 0), ("", "deps bad", 1)]),
    )
    conn = _Conn({"command -v sudo": _result()})
    with pytest.raises(HTTPException) as exc_info:
        await setup._install_setup_dependencies(_context(conn))
    assert exc_info.value.status_code == 500

    monkeypatch.setattr(setup, "SETUP_OPTIONAL_PACKAGES", ())
    monkeypatch.setattr(setup, "SEVEN_ZIP_PACKAGE_ALTERNATIVES", ())
    monkeypatch.setattr(
        setup, "run_apt_command_with_retry", AsyncMock(side_effect=[("", "", 0), ("", "", 0)])
    )
    await setup._install_setup_dependencies(_context(_Conn({"command -v sudo": _result()})))


@pytest.mark.asyncio
async def test_legacy_libssl_directory_user_and_firewall_failures(monkeypatch):
    conn = _Conn({"id cs2server": _result(exit_status=0)})
    conn.start_sftp_client = lambda: _SftpContext()
    context = _context(conn, os_version="24.04")
    monkeypatch.setattr("os.path.exists", lambda path: path.endswith("amd64.deb"))
    monkeypatch.setattr(
        setup, "_run_setup_command", AsyncMock(side_effect=[("installed\n", "", 0), ("", "", 0)])
    )
    await setup._install_legacy_libssl(context)
    assert any("installed successfully" in line for line in context.logs)
    context = _context(conn, os_version="24.04")
    monkeypatch.setattr(setup, "_run_setup_command", AsyncMock(side_effect=RuntimeError("sftp")))
    await setup._install_legacy_libssl(context)
    assert any("installation error" in line for line in context.logs)

    conn = _Conn({"id cs2server": _result(exit_status=0)})
    context = _context(conn)
    monkeypatch.setattr(setup, "_run_setup_command", AsyncMock(side_effect=[("", "", 1)]))
    with pytest.raises(HTTPException, match="Failed to set the CS2 user password"):
        await setup._ensure_setup_user(context)

    monkeypatch.setattr(
        setup, "_run_setup_command", AsyncMock(side_effect=[("", "", 0), ("", "mkdir", 1)])
    )
    with pytest.raises(HTTPException, match="Failed to create the game directory"):
        await setup._configure_setup_directory(_context(_Conn()))

    firewall = _context(_Conn(), request=_request(open_game_ports=True))
    monkeypatch.setattr(
        setup,
        "_run_setup_command",
        AsyncMock(side_effect=[("Status: active", "", 0), ("", "denied", 1)]),
    )
    await setup._configure_setup_firewall(firewall)
    assert any("Failed to open ports" in line for line in firewall.logs)
    inactive = _context(_Conn(), request=_request(open_game_ports=True))
    monkeypatch.setattr(
        setup, "_run_setup_command", AsyncMock(return_value=("Status: inactive", "", 0))
    )
    await setup._configure_setup_firewall(inactive)
    assert any("is not enabled" in line for line in inactive.logs)


@pytest.mark.asyncio
async def test_persistence_failures_are_non_fatal(monkeypatch):
    context = _context(_Conn(), needs_sudo=True, sudo_password=None)
    monkeypatch.setattr(setup, "save_initialized_server", AsyncMock(side_effect=RuntimeError("db")))
    monkeypatch.setattr(
        setup.redis_manager, "set_initialized_server", AsyncMock(side_effect=RuntimeError("redis"))
    )
    assert (
        await setup._persist_setup_configuration(
            context, current_user=SimpleNamespace(id=1), db=object()
        )
        is None
    )
    assert any("Database save failed" in line for line in context.logs)
    assert any("Failed to save configuration" in line for line in context.logs)


@pytest.mark.asyncio
async def test_apt_retry_clamps_delay_and_handles_timeout(monkeypatch):
    runner = AsyncMock(
        side_effect=[asyncio.TimeoutError(), asyncio.TimeoutError(), asyncio.TimeoutError()]
    )
    monkeypatch.setattr(setup, "run_admin_command", runner)
    monkeypatch.setattr(setup.asyncio, "sleep", AsyncMock())
    result = await setup.run_apt_command_with_retry(
        _Conn(),
        "apt",
        description="apt",
        needs_sudo=False,
        sudo_password=None,
        add_log=AsyncMock(),
        attempts=3,
    )
    assert result[2] == 124 and runner.await_count == 3
