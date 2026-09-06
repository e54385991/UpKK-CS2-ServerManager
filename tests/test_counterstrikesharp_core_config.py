"""每次安装或升级 CounterStrikeSharp 都要把 core.json 的守则开关关掉。"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.plugins import counterstrikesharp_core as core
from services.ssh_manager import SSHManager

CSGO_DIR = "/home/steam/cs2server/cs2/game/csgo"
CONFIG_PATH = f"{CSGO_DIR}/{core.CORE_CONFIG_PATH}"
EXAMPLE_PATH = f"{CSGO_DIR}/{core.CORE_EXAMPLE_PATH}"

EXAMPLE_CONFIG = json.dumps(
    {
        "PublicChatTrigger": ["!"],
        "FollowCS2ServerGuidelines": True,
        "PluginHotReloadEnabled": True,
        "ServerLanguage": "en",
    },
    indent=4,
)


class _Host:
    """Minimal remote host: answers the read/write shell commands in-memory."""

    def __init__(
        self,
        files: dict[str, str] | None = None,
        *,
        write_fails: bool = False,
        read_fails: bool = False,
    ):
        self.files = dict(files or {})
        self.write_fails = write_fails
        self.read_fails = read_fails
        self.commands: list[str] = []

    async def execute_command(self, command: str, timeout: int | None = None):
        self.commands.append(command)

        if command.startswith("if [ -f "):
            for path, content in self.files.items():
                if f" {path} " in command:
                    if self.read_fails:
                        return False, "", "permission denied"
                    payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
                    return True, payload, ""
            return True, core.ABSENT_MARKER, ""

        if command.startswith("if test -e "):
            present = any(f" {path};" in command or f" {path} " in command for path in self.files)
            return True, "yes" if present else "no", ""

        if "base64 -d >" in command:
            if self.write_fails:
                return False, "", "read-only file system"
            payload = command.split("printf %s ", 1)[1].split(" |", 1)[0].strip("'")
            target = command.rsplit("mv -f ", 1)[1].split()[-1].strip("'")
            self.files[target] = base64.b64decode(payload).decode("utf-8")
            return True, "", ""

        return True, "", ""


def test_repository_detection_matches_only_counterstrikesharp():
    assert core.is_counterstrikesharp_repository("https://github.com/roflmuffin/CounterStrikeSharp")
    assert core.is_counterstrikesharp_repository(
        "https://github.com/roflmuffin/CounterStrikeSharp/releases/download/v1/x.zip"
    )
    assert core.is_counterstrikesharp_repository("git@github.com:roflmuffin/CounterStrikeSharp.git")
    # A plugin repository whose name merely starts the same must not match.
    assert not core.is_counterstrikesharp_repository(
        "https://github.com/roflmuffin/CounterStrikeSharp-Examples"
    )
    assert not core.is_counterstrikesharp_repository("https://github.com/Source2ZE/CS2Fixes")
    assert not core.is_counterstrikesharp_repository(None)


@pytest.mark.asyncio
async def test_fresh_install_seeds_core_json_from_the_shipped_example():
    host = _Host({EXAMPLE_PATH: EXAMPLE_CONFIG})

    result = await core.apply_counterstrikesharp_core_defaults(host.execute_command, CSGO_DIR)

    assert result.applied
    written = json.loads(host.files[CONFIG_PATH])
    assert written[core.GUIDELINES_KEY] is False
    # The rest of CounterStrikeSharp's defaults survive the rewrite.
    assert written["PluginHotReloadEnabled"] is True
    assert written["PublicChatTrigger"] == ["!"]


@pytest.mark.asyncio
async def test_upgrade_disables_the_flag_without_touching_other_keys():
    existing = json.dumps(
        {"FollowCS2ServerGuidelines": True, "ServerLanguage": "zh", "Custom": 7}, indent=4
    )
    host = _Host({CONFIG_PATH: existing})

    result = await core.apply_counterstrikesharp_core_defaults(host.execute_command, CSGO_DIR)

    assert result.applied
    written = json.loads(host.files[CONFIG_PATH])
    assert written == {
        "FollowCS2ServerGuidelines": False,
        "ServerLanguage": "zh",
        "Custom": 7,
    }


@pytest.mark.asyncio
async def test_an_already_disabled_config_is_left_untouched():
    existing = json.dumps({"FollowCS2ServerGuidelines": False, "ServerLanguage": "zh"}, indent=2)
    host = _Host({CONFIG_PATH: existing})

    result = await core.apply_counterstrikesharp_core_defaults(host.execute_command, CSGO_DIR)

    assert not result.applied and result.message == "already false"
    # Byte-identical: no reformatting of an operator's file for no reason.
    assert host.files[CONFIG_PATH] == existing
    assert not any("base64 -d >" in command for command in host.commands)


@pytest.mark.asyncio
async def test_unparseable_config_is_patched_in_place_rather_than_reformatted():
    existing = '{\n    // keep comments\n    "FollowCS2ServerGuidelines": true,\n}\n'
    host = _Host({CONFIG_PATH: existing})

    result = await core.apply_counterstrikesharp_core_defaults(host.execute_command, CSGO_DIR)

    assert result.applied
    assert "// keep comments" in host.files[CONFIG_PATH]
    assert '"FollowCS2ServerGuidelines": false' in host.files[CONFIG_PATH]


@pytest.mark.asyncio
async def test_a_write_failure_is_reported_without_raising():
    reports: list[str] = []

    async def report(message: str) -> None:
        reports.append(message)

    host = _Host({CONFIG_PATH: EXAMPLE_CONFIG}, write_fails=True)
    result = await core.apply_counterstrikesharp_core_defaults(
        host.execute_command, CSGO_DIR, report=report
    )

    assert not result.applied
    assert "read-only file system" in result.message
    assert any("⚠" in message for message in reports)


@pytest.mark.asyncio
async def test_an_unreadable_config_is_never_replaced_by_the_example():
    host = _Host({CONFIG_PATH: EXAMPLE_CONFIG, EXAMPLE_PATH: EXAMPLE_CONFIG}, read_fails=True)

    result = await core.apply_counterstrikesharp_core_defaults(host.execute_command, CSGO_DIR)

    assert not result.applied and "permission denied" in result.message
    assert host.files[CONFIG_PATH] == EXAMPLE_CONFIG
    assert not any("base64 -d >" in command for command in host.commands)


@pytest.mark.asyncio
async def test_marketplace_install_of_the_framework_applies_the_default():
    host = _Host({EXAMPLE_PATH: EXAMPLE_CONFIG})

    result = await core.maybe_apply_counterstrikesharp_core_defaults(
        host.execute_command,
        csgo_dir=CSGO_DIR,
        source_dir="/tmp/upkk-plugin-3-op/extract",
        repo_url="https://github.com/roflmuffin/CounterStrikeSharp",
    )

    assert result.applied
    assert json.loads(host.files[CONFIG_PATH])[core.GUIDELINES_KEY] is False


@pytest.mark.asyncio
async def test_an_archive_carrying_the_runtime_is_recognized_without_a_repo_url():
    source_dir = "/tmp/upkk-plugin-3-op/extract"
    host = _Host(
        {
            EXAMPLE_PATH: EXAMPLE_CONFIG,
            f"{source_dir}/addons/counterstrikesharp/dotnet": "",
        }
    )

    result = await core.maybe_apply_counterstrikesharp_core_defaults(
        host.execute_command,
        csgo_dir=CSGO_DIR,
        source_dir=source_dir,
        repo_url="https://github.com/acme/mirror",
    )

    assert result.applied


@pytest.mark.asyncio
async def test_a_plain_plugin_install_leaves_core_json_alone():
    existing = json.dumps({"FollowCS2ServerGuidelines": True}, indent=4)
    host = _Host({CONFIG_PATH: existing})

    result = await core.maybe_apply_counterstrikesharp_core_defaults(
        host.execute_command,
        csgo_dir=CSGO_DIR,
        source_dir="/tmp/upkk-plugin-3-op/extract",
        repo_url="https://github.com/acme/AdminMenu",
        download_url="https://github.com/acme/AdminMenu/releases/download/v1/AdminMenu.zip",
    )

    assert not result.applied
    assert host.files[CONFIG_PATH] == existing


@pytest.mark.asyncio
async def test_framework_install_writes_core_json_before_reporting_success():
    """安装框架（以及走同一路径的升级）必须把守则开关写进 core.json。"""
    server = SimpleNamespace(
        id=8,
        user_id=3,
        game_directory="/srv/cs2",
        use_panel_proxy=False,
        github_proxy="",
        sudo_password=None,
    )
    csgo_dir = f"{server.game_directory}/cs2/game/csgo"
    host = _Host({f"{csgo_dir}/{core.CORE_EXAMPLE_PATH}": EXAMPLE_CONFIG})

    manager = SSHManager(use_pool=False)
    manager.connect = AsyncMock(return_value=(True, "ok"))
    manager.disconnect = AsyncMock()
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))

    async def execute_command(command: str, **kwargs):
        if command.startswith(("if [ -f ", "if test -e ")) or "base64 -d >" in command:
            return await host.execute_command(command)
        if "browser_download_url" in command:
            return True, "https://github.com/css.zip", ""
        if "stat -" in command:
            return True, "20000", ""
        # Every probe in the install is `test ... && echo '<token>'`.
        if "&& echo '" in command:
            return True, command.split("&& echo '", 1)[1].split("'", 1)[0], ""
        return True, "", ""

    manager.execute_command = execute_command
    progress: list[str] = []

    success, message = await manager.install_counterstrikesharp(server, progress.append)

    assert success, message
    written = json.loads(host.files[f"{csgo_dir}/{core.CORE_CONFIG_PATH}"])
    assert written[core.GUIDELINES_KEY] is False
    assert written["PluginHotReloadEnabled"] is True
    assert any(core.GUIDELINES_KEY in line for line in progress)
