"""Coverage for detect → auto-install → verify host initialization."""

from __future__ import annotations

import pytest

from services.host_initialization import HostDependencyResult, ensure_steamcmd_packages
from services.system_dependencies import STEAMCMD_REQUIRED_PACKAGES, manual_install_command


class ScriptedRunner:
    def __init__(self, run_results, privileged_results=None, privilege="root"):
        self.run_results = list(run_results)
        self.privileged_results = list(privileged_results or [])
        self.privilege = privilege
        self.commands: list[str] = []
        self.privileged: list[str] = []

    async def run(self, command, *, timeout=60):
        self.commands.append(command)
        return self.run_results.pop(0)

    async def run_privileged(self, command, *, timeout=600):
        self.privileged.append(command)
        if self.privileged_results:
            return self.privileged_results.pop(0)
        return self.run_results.pop(0)

    async def resolve_privilege(self):
        return self.privilege


def _verify_missing(*packages: str):
    listed = " ".join(packages)
    return (1, "", f"Missing required packages: {listed}")


def _os_release(*, ubuntu: bool = False):
    if ubuntu:
        return (0, "ID=ubuntu\nVERSION_CODENAME=noble\nVERSION_ID=24.04\n", "")
    return (0, "ID=unknown\n", "")


@pytest.mark.asyncio
async def test_ensure_skips_install_when_packages_are_present():
    runner = ScriptedRunner(
        [
            (0, "amd64\n", ""),
            (0, "/usr/bin/apt-get\n", ""),
            (0, "", ""),
        ]
    )

    result = await ensure_steamcmd_packages(runner, ("lib32z1",))

    assert result.success is True
    assert result.installed is False
    assert runner.privileged == []


@pytest.mark.asyncio
async def test_ensure_installs_only_missing_packages_as_root():
    runner = ScriptedRunner(
        [
            (0, "amd64\n", ""),
            (0, "/usr/bin/apt-get\n", ""),
            _verify_missing("lib32z1", "libc6-i386"),
            (0, "i386\n", ""),
            _os_release(),
            (0, "", ""),
        ],
        privileged_results=[
            (0, "updated\n", ""),
            (0, "installed\n", ""),
        ],
    )

    result = await ensure_steamcmd_packages(runner, STEAMCMD_REQUIRED_PACKAGES)

    assert result.success is True
    assert result.installed is True
    assert result.missing_before == ("lib32z1", "libc6-i386")
    assert any("lib32z1" in command and "libc6-i386" in command for command in runner.privileged)
    assert not any("lib32gcc-s1" in command for command in runner.privileged)


@pytest.mark.asyncio
async def test_ensure_returns_manual_command_when_install_fails(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("services.host_initialization.asyncio.sleep", no_sleep)
    runner = ScriptedRunner(
        [
            (0, "amd64\n", ""),
            (0, "/usr/bin/apt-get\n", ""),
            _verify_missing("lib32z1"),
            (0, "i386\n", ""),
            _os_release(),
            _verify_missing("lib32z1"),
        ],
        privileged_results=[
            (0, "updated\n", ""),
            (1, "", "E: Unable to locate package lib32z1"),
            (1, "", "E: Unable to locate package lib32z1"),
            (1, "", "E: Unable to locate package lib32z1"),
        ],
        privilege="sudo",
    )

    result = await ensure_steamcmd_packages(runner, ("lib32z1", "libc6-i386"))

    assert result.success is False
    assert result.missing_after == ("lib32z1",)
    assert result.manual_install_command == "sudo apt-get install -y lib32z1"
    assert "sudo apt-get install -y lib32z1" in result.message


@pytest.mark.asyncio
async def test_ensure_asks_for_manual_install_without_privilege():
    runner = ScriptedRunner(
        [
            (0, "amd64\n", ""),
            (0, "/usr/bin/apt-get\n", ""),
            _verify_missing("lib32gcc-s1"),
        ],
        privilege="none",
    )

    result = await ensure_steamcmd_packages(runner, ("lib32gcc-s1",))

    assert result.success is False
    assert result.privilege == "none"
    assert runner.privileged == []
    assert result.manual_install_command == manual_install_command(("lib32gcc-s1",))
    assert "Could not elevate" in result.message


@pytest.mark.asyncio
async def test_ensure_rejects_arm_before_touching_packages():
    runner = ScriptedRunner([(0, "aarch64\n", "")])

    result = await ensure_steamcmd_packages(runner)

    assert result.success is False
    assert result.architecture_supported is False
    assert result.manual_install_command is None
    assert runner.commands == ["dpkg --print-architecture 2>/dev/null || uname -m"]


@pytest.mark.asyncio
async def test_ensure_retries_next_mirror_after_apt_source_failure(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("services.host_initialization.asyncio.sleep", no_sleep)
    runner = ScriptedRunner(
        [
            (0, "amd64\n", ""),
            (0, "/usr/bin/apt-get\n", ""),
            _verify_missing("lib32z1"),
            (0, "i386\n", ""),
            _os_release(ubuntu=True),
            _verify_missing("lib32z1"),
            (0, "", ""),
        ],
        privileged_results=[
            (0, "applied:official\n", ""),
            (1, "", "E: Failed to fetch http://archive.ubuntu.com/ubuntu InRelease"),
            (0, "applied:ustc\n", ""),
            (0, "updated\n", ""),
            (0, "installed\n", ""),
        ],
        privilege="root",
    )

    result = await ensure_steamcmd_packages(runner, ("lib32z1",), preferred_mirror="official")

    assert result.success is True
    assert result.apt_mirror == "ustc"
    assert "official" in result.failed_mirrors
    assert any("mirrors.ustc.edu.cn" in command for command in runner.privileged)
    assert any("Switching apt sources to USTC" in line for line in result.logs)


def test_ready_factory_has_no_manual_command():
    result = HostDependencyResult.ready(architecture="amd64")
    assert result.success is True
    assert result.manual_install_command is None
