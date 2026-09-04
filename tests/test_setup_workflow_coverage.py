"""Cover setup workflow decisions with an in-memory SSH command fake."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import setup_workflow as setup


class _Conn:
    def __init__(self, results=None):
        self.results = dict(results or {})
        self.calls: list[str] = []

    async def run(self, command, **_kwargs):
        self.calls.append(command)
        value = self.results.get(command)
        if callable(value):
            value = value(command)
        return value or SimpleNamespace(stdout="", stderr="", exit_status=0)


def _result(stdout="", stderr="", exit_status=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=exit_status)


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
    logs: list[str] = []
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


def test_password_generation_and_command_wrappers_are_safe():
    password = setup.generate_secure_password(16)
    assert len(password) == 16
    assert any(item.islower() for item in password)
    assert any(item.isupper() for item in password)
    assert any(item.isdigit() for item in password)
    assert setup._short_command_error("", "") == "The command returned no error details"
    assert setup._short_command_error("out", "err") == "err\nout"


@pytest.mark.asyncio
async def test_setup_host_detection_covers_root_and_sudo_strategies(monkeypatch):
    conn = _Conn(
        {
            "dpkg --print-architecture 2>/dev/null || uname -m": _result("amd64\n"),
            "command -v apt-get": _result("/usr/bin/apt-get"),
            "lsb_release -rs 2>/dev/null || sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '\"'": _result(
                "24.04\n"
            ),
            "whoami": _result("root\n"),
        }
    )
    context = _context(conn)
    await setup._detect_setup_host(context)
    assert context.os_version == "24.04" and context.needs_sudo is False

    conn = _Conn(
        {
            "dpkg --print-architecture 2>/dev/null || uname -m": _result("x86_64\n"),
            "command -v apt-get": _result("apt"),
            "lsb_release -rs 2>/dev/null || sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '\"'": _result(
                "22.04"
            ),
            "whoami": _result("steam"),
        }
    )
    monkeypatch.setattr(
        setup,
        "run_sudo_command",
        AsyncMock(side_effect=[("", "", 0)]),
    )
    context = _context(conn)
    await setup._detect_setup_host(context)
    assert context.needs_sudo and context.sudo_password == "sudo-pass"

    conn = _Conn({"dpkg --print-architecture 2>/dev/null || uname -m": _result("arm64")})
    with pytest.raises(HTTPException, match="Unsupported server architecture"):
        await setup._detect_setup_host(_context(conn))
    conn = _Conn(
        {
            "dpkg --print-architecture 2>/dev/null || uname -m": _result("amd64"),
            "command -v apt-get": _result(exit_status=1),
        }
    )
    with pytest.raises(HTTPException, match="apt-get"):
        await setup._detect_setup_host(_context(conn))


@pytest.mark.asyncio
async def test_setup_sudo_and_admin_commands_decode_results():
    conn = _Conn()
    conn.run = AsyncMock(return_value=_result("out", "err", 3))
    assert await setup.run_sudo_command(conn, "id") == ("out", "err", 3)
    assert await setup.run_sudo_command(conn, "id", "pw") == ("out", "err", 3)
    assert await setup.run_admin_command(conn, "id", needs_sudo=False, sudo_password=None) == (
        "out",
        "err",
        3,
    )


@pytest.mark.asyncio
async def test_setup_user_directory_firewall_and_persistence(monkeypatch):
    conn = _Conn()
    context = _context(conn)
    conn.run = AsyncMock(return_value=_result("", "", 1))
    monkeypatch.setattr(setup, "run_admin_command", AsyncMock(return_value=("", "", 0)))
    await setup._ensure_setup_user(context)
    assert any("Creating user" in line for line in context.logs)
    await setup._configure_setup_directory(context)
    assert context.game_directory == "/home/cs2server/cs2"

    context.request = _request(open_game_ports=False)
    await setup._configure_setup_firewall(context)
    context.request = _request(open_game_ports=True)
    monkeypatch.setattr(setup, "_run_setup_command", AsyncMock(return_value=("", "", 0)))
    await setup._configure_setup_firewall(context)

    monkeypatch.setattr(setup.SSHServerSudo, "upsert", AsyncMock())
    monkeypatch.setattr(
        setup.redis_manager, "set_initialized_server", AsyncMock(return_value="init-1")
    )
    context.game_directory = "/home/cs2server/cs2"
    result = await setup._persist_setup_configuration(
        context, current_user=SimpleNamespace(id=1), db=object()
    )
    assert result == "init-1"
    context.request = _request(save_config=False)
    assert (
        await setup._persist_setup_configuration(
            context, current_user=SimpleNamespace(id=1), db=object()
        )
        is None
    )


@pytest.mark.asyncio
async def test_setup_apt_retry_covers_timeout_success_and_exhaustion(monkeypatch):
    conn = _Conn()
    add_log = AsyncMock()
    runner = AsyncMock(side_effect=[asyncio.TimeoutError(), ("", "", 0)])
    monkeypatch.setattr(setup, "run_admin_command", runner)
    monkeypatch.setattr(setup.asyncio, "sleep", AsyncMock())
    result = await setup.run_apt_command_with_retry(
        conn,
        "apt-get update",
        description="update",
        needs_sudo=False,
        sudo_password=None,
        add_log=add_log,
        attempts=2,
    )
    assert result == ("", "", 0)

    runner.side_effect = [("", "bad", 1), ("", "bad", 1)]
    result = await setup.run_apt_command_with_retry(
        conn,
        "apt-get update",
        description="update",
        needs_sudo=False,
        sudo_password=None,
        add_log=add_log,
        attempts=2,
    )
    assert result[2] == 1 and add_log.await_count >= 2
