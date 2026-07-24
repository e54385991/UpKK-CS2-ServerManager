"""Regression tests for privileged remote-command credential boundaries."""

import shlex
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.setup import run_sudo_command
from services.ssh_manager import SSHManager


def _connection():
    return SimpleNamespace(
        run=AsyncMock(
            return_value=SimpleNamespace(
                stdout="ok",
                stderr="",
                exit_status=0,
            )
        )
    )


@pytest.mark.asyncio
async def test_setup_sudo_password_is_sent_over_stdin_not_the_command_line():
    connection = _connection()
    password = "quote' newline\n $(touch /tmp/password-owned)"
    command = "apt-get update && apt-get install -y unzip"

    assert await run_sudo_command(connection, command, password) == ("ok", "", 0)

    remote_command = connection.run.await_args.args[0]
    assert remote_command == f"sudo -S -- sh -c {shlex.quote(command)}"
    assert password not in remote_command
    assert connection.run.await_args.kwargs == {
        "input": f"{password}\n",
        "check": False,
    }


@pytest.mark.asyncio
async def test_ssh_manager_sudo_password_is_sent_over_stdin_not_the_command_line():
    connection = _connection()
    password = "single'quote; id\nsecond-line"
    command = "chown -R cs2server:cs2server /home/cs2server && chmod 755 /home/cs2server"
    manager = SSHManager()
    manager.conn = connection

    assert await manager.execute_sudo_command(command, password) == (True, "ok", "")

    remote_command = connection.run.await_args.args[0]
    assert remote_command == f"sudo -S -- sh -c {shlex.quote(command)}"
    assert password not in remote_command
    assert connection.run.await_args.kwargs == {
        "check": False,
        "input": f"{password}\n",
    }
