"""Regression tests for upgrade exclusions around framework gamedata."""

import pytest

from api.routes.github_plugins import _build_plugin_copy_command
from modules.models import AuthType, Server
from services.ssh_manager import SSHManager


def _assert_forced_gamedata_copy(command: str) -> None:
    primary, forced = command.rsplit(" && cd ", 1)
    assert primary
    assert "find . -path '*/gamedata/*' -type f" in forced
    assert 'relative=${source#./}' in forced
    assert "--no-dereference --remove-destination" in forced
    assert '"$source" "$destination"' in forced
    assert forced.endswith("{} +")


def test_rsync_exclusions_are_followed_by_forced_gamedata_copy():
    command = _build_plugin_copy_command(
        "/tmp/package root",
        "/srv/cs2/game/csgo",
        ["*.json", "*.jsonc", "cfg/"],
        use_rsync=True,
    )

    assert command.startswith("rsync -av")
    assert "--exclude='*.json'" in command
    assert "--exclude='*.jsonc'" in command
    assert "--exclude=cfg/" in command
    _assert_forced_gamedata_copy(command)


def test_tar_fallback_exclusions_are_followed_by_forced_gamedata_copy():
    command = _build_plugin_copy_command(
        "/tmp/package",
        "/srv/cs2/game/csgo",
        ["*.json", "*.jsonc"],
        use_rsync=False,
    )

    assert command.startswith("cd /tmp/package && tar ")
    assert "--exclude='*.json'" in command
    assert "--exclude='*.jsonc'" in command
    _assert_forced_gamedata_copy(command)


def test_copy_without_exclusions_still_forces_gamedata_refresh():
    command = _build_plugin_copy_command(
        "/tmp/package",
        "/srv/cs2/game/csgo",
        [],
        use_rsync=False,
    )

    assert command.startswith("cp -r /tmp/package/. /srv/cs2/game/csgo/")
    _assert_forced_gamedata_copy(command)


def test_copy_command_shell_quotes_paths_and_patterns():
    command = _build_plugin_copy_command(
        "/tmp/package with spaces",
        "/srv/game with spaces/csgo",
        ['*.json"; touch /tmp/unsafe; #'],
        use_rsync=True,
    )

    assert "'/tmp/package with spaces'/" in command
    assert "'/srv/game with spaces/csgo'/" in command
    assert "--exclude='*.json\"; touch /tmp/unsafe; #'" in command
    _assert_forced_gamedata_copy(command)


class _FailingCounterStrikeSharpExtraction(SSHManager):
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.disconnected = False

    async def connect(self, server):
        return True, "Connected"

    async def disconnect(self):
        self.disconnected = True

    async def execute_command_streaming(self, command, **kwargs):
        self.commands.append(command)
        return True, "", ""

    async def execute_command(self, command, **kwargs):
        self.commands.append(command)
        if "releases/latest" in command:
            return (
                True,
                "https://github.com/roflmuffin/CounterStrikeSharp/"
                "releases/download/v1/counterstrikesharp-with-runtime-linux-v1.zip\n",
                "",
            )
        if command.startswith("test -d "):
            return True, "exists\n", ""
        if command.startswith("test -f "):
            return True, "exists\n", ""
        if command.startswith("stat "):
            return True, "20000\n", ""
        if command == "command -v unzip":
            return True, "/usr/bin/unzip\n", ""
        if command.startswith("unzip -o "):
            return False, "", "permission denied"
        return True, "", ""


@pytest.mark.asyncio
async def test_counterstrikesharp_update_fails_when_unzip_fails():
    server = Server(
        id=42,
        user_id=7,
        name="CSS",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        game_directory="/srv/css",
    )
    manager = _FailingCounterStrikeSharpExtraction()

    success, message = await manager.update_counterstrikesharp(server)

    assert success is False
    assert "permission denied" in message
    assert manager.disconnected is True
    unzip_index = next(
        index for index, command in enumerate(manager.commands)
        if command.startswith("unzip -o ")
    )
    assert not any(
        "addons/counterstrikesharp" in command
        for command in manager.commands[unzip_index + 1:]
    )
