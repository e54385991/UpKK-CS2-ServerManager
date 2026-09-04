"""Regression coverage for resilient Setup Wizard dependency handling."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routes import setup
from services.ssh_manager import SSHManager
from services.system_dependencies import (
    STEAMCMD_REQUIRED_PACKAGES,
    apt_get_command,
    installed_packages_verification_command,
    manual_install_command,
    normalize_debian_architecture,
    parse_missing_packages,
    steamcmd_architecture_supported,
)


class _FakeDatabase:
    async def commit(self):
        return None


class _SetupConnection:
    def __init__(self, *, update_results=None, verification_success=True):
        self.update_results = list(update_results or [("updated", "", 0)])
        self.verification_success = verification_success
        self.commands = []
        self.closed = False

    async def run(self, command, **_kwargs):
        self.commands.append(command)
        if command.startswith("dpkg --print-architecture"):
            return SimpleNamespace(stdout="amd64\n", stderr="", exit_status=0)
        if command == "command -v apt-get":
            return SimpleNamespace(stdout="/usr/bin/apt-get\n", stderr="", exit_status=0)
        if command.startswith("lsb_release -rs"):
            return SimpleNamespace(stdout="26.04\n", stderr="", exit_status=0)
        if command == "whoami":
            return SimpleNamespace(stdout="root\n", stderr="", exit_status=0)
        if command == "command -v sudo":
            return SimpleNamespace(stdout="/usr/bin/sudo\n", stderr="", exit_status=0)
        if command.startswith("env DEBIAN_FRONTEND=noninteractive apt-get"):
            if command.endswith(" update"):
                stdout, stderr, exit_status = self.update_results.pop(0)
                return SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=exit_status)
            return SimpleNamespace(stdout="installed\n", stderr="", exit_status=0)
        if command.startswith("missing=''; for package in"):
            if self.verification_success:
                return SimpleNamespace(stdout="", stderr="", exit_status=0)
            return SimpleNamespace(
                stdout="", stderr="Missing required packages: lib32z1\n", exit_status=1
            )
        raise AssertionError(f"Unexpected remote command: {command}")

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def _setup_request() -> setup.ServerSetupRequest:
    return setup.ServerSetupRequest(
        name="Ubuntu 26.04",
        host="192.0.2.10",
        ssh_user="root",
        ssh_password="secret",
        captcha_token="token",
        captcha_code="1234",
        save_config=False,
        open_game_ports=False,
    )


async def _allow_captcha(*_args, **_kwargs):
    return True


async def _no_sleep(_delay):
    return None


@pytest.mark.asyncio
async def test_setup_retries_apt_update_then_fails_closed(monkeypatch):
    connection = _SetupConnection(update_results=[("", "temporary mirror failure", 100)] * 3)

    async def connect(**_kwargs):
        return connection

    monkeypatch.setattr(setup.captcha_service, "validate_captcha", _allow_captcha)
    monkeypatch.setattr(setup.asyncssh, "connect", connect)
    monkeypatch.setattr(setup.asyncio, "sleep", _no_sleep)

    with pytest.raises(HTTPException) as exc_info:
        await setup.auto_setup_server(
            _setup_request(), current_user=SimpleNamespace(id=7), db=_FakeDatabase()
        )

    assert exc_info.value.status_code == 502
    assert "after automatic retries" in exc_info.value.detail
    assert sum(command.endswith(" update") for command in connection.commands) == 3
    assert connection.closed is True


@pytest.mark.asyncio
async def test_setup_rejects_false_positive_apt_install(monkeypatch):
    connection = _SetupConnection(verification_success=False)

    async def connect(**_kwargs):
        return connection

    monkeypatch.setattr(setup.captcha_service, "validate_captcha", _allow_captcha)
    monkeypatch.setattr(setup.asyncssh, "connect", connect)

    with pytest.raises(HTTPException) as exc_info:
        await setup.auto_setup_server(
            _setup_request(), current_user=SimpleNamespace(id=7), db=_FakeDatabase()
        )

    assert exc_info.value.status_code == 500
    assert "dependency verification failed" in exc_info.value.detail
    assert "lib32z1" in exc_info.value.detail


def test_dependency_contract_targets_ubuntu_2604_amd64():
    assert normalize_debian_architecture("x86_64\n") == "amd64"
    assert steamcmd_architecture_supported("amd64") is True
    assert steamcmd_architecture_supported("aarch64") is False
    assert "DPkg::Lock::Timeout=120" in apt_get_command("update")
    assert "Acquire::Retries=3" in apt_get_command("install", ("lib32gcc-s1",))
    assert {"libc6-i386", "lib32gcc-s1", "lib32stdc++6", "lib32z1"}.issubset(
        STEAMCMD_REQUIRED_PACKAGES
    )
    verification = installed_packages_verification_command(STEAMCMD_REQUIRED_PACKAGES)
    assert "Missing required packages" in verification
    assert parse_missing_packages("", "Missing required packages: lib32z1 libc6-i386") == [
        "lib32z1",
        "libc6-i386",
    ]
    assert manual_install_command(("lib32z1",)) == "sudo apt-get install -y lib32z1"


class _PreflightManager(SSHManager):
    def __init__(self, results):
        super().__init__()
        self.results = iter(results)
        self.commands = []

    async def execute_command(self, command, *_args, **_kwargs):
        self.commands.append(command)
        return next(self.results)


@pytest.mark.asyncio
async def test_deployment_preflight_rejects_arm_before_runtime_probe():
    manager = _PreflightManager([(True, "arm64\n", "")])
    manager.current_server = SimpleNamespace(sudo_password=None, ssh_password="secret")

    success, message = await manager._steamcmd_host_preflight_connected()

    assert success is False
    assert "arm64" in message
    assert "amd64" in message
    assert len(manager.commands) == 1


@pytest.mark.asyncio
async def test_deployment_preflight_requires_a_connected_server():
    manager = _PreflightManager([])
    manager.current_server = None

    success, message = await manager._steamcmd_host_preflight_connected()

    assert success is False
    assert "Not connected" in message


def test_manual_setup_script_contains_retry_architecture_and_runtime_guards():
    from services.server_setup_script import build_manual_setup_script

    script = build_manual_setup_script(cs2_username="cs2server", password="Pw1!secret")

    assert "DPkg::Lock::Timeout=120" in script
    assert "Acquire::Retries=3" in script
    assert "amd64|x86_64) ;;" in script
    assert "libc6-i386 lib32gcc-s1 lib32stdc++6 lib32z1" in script
    assert "Required dependency verification failed" in script
    template = (
        Path(__file__).resolve().parents[1] / "templates" / "server_setup_wizard.html"
    ).read_text()
    assert "DPkg::Lock::Timeout=120" in template
