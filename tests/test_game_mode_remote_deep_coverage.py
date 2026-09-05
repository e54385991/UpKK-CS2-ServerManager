"""覆盖游戏模式远程文件操作的安全和异常分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import game_mode_remote as remote


class _Manager:
    def __init__(self, results=()):
        self.results = list(results)
        self.commands = []
        self.disconnect = AsyncMock()

    async def execute_command(self, command, **_kwargs):
        self.commands.append(command)
        value = self.results.pop(0) if self.results else (True, "", "")
        if isinstance(value, BaseException):
            raise value
        return value

    async def write_file(self, *_args):
        return True, ""


def _server(**overrides):
    values = {"game_directory": "/srv/cs2", "id": 1}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_remote_path_helpers_reject_unsafe_inputs_and_build_all_paths():
    with pytest.raises(remote.GameModeRemoteError):
        remote.resolve_csgo_directory("")
    with pytest.raises(remote.GameModeRemoteError):
        remote.resolve_csgo_directory("/srv/../etc")
    paths = remote.remote_paths(_server(game_directory="/srv/cs2"))
    assert paths["addons"].endswith("/cs2/game/csgo/addons")
    assert paths["mapchooser_dll"].endswith("MapChooser/MapChooser.dll")
    assert remote.wait_file_paths(_server(), ("configs/a", "/maps/b"))[1].endswith("maps/b")
    with pytest.raises(remote.GameModeRemoteError):
        remote.wait_file_paths(_server(), ("../escape",))
    with pytest.raises(remote.GameModeRemoteError):
        remote.resolve_addons_directory("/srv/../escape")


@pytest.mark.asyncio
async def test_connect_and_inspect_parse_success_and_remote_failure():
    manager = _Manager()
    manager.connect = AsyncMock(return_value=(True, "connected"))
    monkeypatch_server = _server()
    original = remote.SSHManager
    remote.SSHManager = lambda: manager
    try:
        assert await remote.connect(monkeypatch_server) is manager
    finally:
        remote.SSHManager = original

    inspect_manager = _Manager([(True, "addons=1\nmalformed\ncss=0\nmapchooser=1\nconfig=0\n", "")])
    values = await remote.inspect_game_mode_state(inspect_manager, _server())
    assert values == {"addons": True, "css": False, "mapchooser": True, "config": False}
    failing = _Manager([(False, "stdout", "remote error")])
    with pytest.raises(remote.GameModeRemoteError, match="remote error"):
        await remote.inspect_game_mode_state(failing, _server())

    failed_manager = _Manager()
    failed_manager.connect = AsyncMock(return_value=(False, "offline"))
    original = remote.SSHManager
    remote.SSHManager = lambda: failed_manager
    try:
        with pytest.raises(remote.GameModeRemoteError, match="offline"):
            await remote.connect(_server())
    finally:
        remote.SSHManager = original


@pytest.mark.asyncio
async def test_wait_wipe_and_replace_cover_empty_bad_and_atomic_cleanup_paths():
    await remote.wait_for_remote_files(_Manager(), [])
    with pytest.raises(remote.GameModeRemoteError, match="Waiting"):
        await remote.wait_for_remote_files(_Manager([(False, "", "Waiting failed")]), ["/tmp/a"])

    progress = AsyncMock()
    clock = iter((0.0, 0.0, 99.0))
    original_monotonic = remote.time.monotonic
    remote.time.monotonic = lambda: next(clock)
    try:
        with pytest.raises(remote.GameModeRemoteError, match="Timed out"):
            await remote.wait_for_remote_files(
                _Manager([(True, "0=0", "")]),
                ["/tmp/config.json"],
                timeout_seconds=10,
                interval_seconds=1,
                sleep=AsyncMock(),
                progress=progress,
            )
    finally:
        remote.time.monotonic = original_monotonic
    progress.assert_awaited()

    good = _Manager([(True, "", "")])
    await remote.wipe_addons_directory(good, "/srv/cs2/cs2/game/csgo/addons")
    assert good.commands and "rm -rf" in good.commands[0]
    for result in ((False, "", "wipe error"), (False, "wipe out", "")):
        with pytest.raises(remote.GameModeRemoteError):
            await remote.wipe_addons_directory(_Manager([result]), "/srv/cs2/cs2/game/csgo/addons")

    with pytest.raises(remote.GameModeRemoteError):
        await remote.wipe_addons_directory(_Manager(), "/srv/cs2/addons")

    with pytest.raises(remote.GameModeRemoteError):
        await remote.replace_remote_file(
            _Manager([(False, "", "mkdir")]), _server(), "/x/a", "x", existed=False
        )
    with pytest.raises(remote.GameModeRemoteError):
        await remote.replace_remote_file(
            _Manager([(True, "", ""), (False, "", "backup")]),
            _server(),
            "/x/a",
            "x",
            existed=True,
        )


@pytest.mark.asyncio
async def test_replace_remote_file_handles_stage_and_move_failures(monkeypatch):
    manager = _Manager([(True, "", ""), (False, "", "move")])
    manager.write_file = AsyncMock(return_value=(True, ""))
    with pytest.raises(remote.GameModeRemoteError, match="move"):
        await remote.replace_remote_file(manager, _server(), "/x/a", "content", existed=False)
    assert any("rm -f" in command for command in manager.commands)

    manager = _Manager([(True, "", "")])
    manager.write_file = AsyncMock(return_value=(False, "write"))
    with pytest.raises(remote.GameModeRemoteError, match="stage"):
        await remote.replace_remote_file(manager, _server(), "/x/a", "x", existed=False)

    manager = _Manager([(True, "", ""), (True, "", "")])
    manager.write_file = AsyncMock(return_value=(True, ""))
    backup = await remote.replace_remote_file(manager, _server(), "/x/a", "content", existed=True)
    assert backup and ".upkk-backup-" in backup
