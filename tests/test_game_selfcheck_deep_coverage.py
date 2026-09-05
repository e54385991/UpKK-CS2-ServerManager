"""覆盖 CS2 自检的自动修复、脚本部署和汇总结果。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh_manager import SSHManager


def _server(**overrides):
    values = dict(id=8, ssh_user="steam", game_directory="/srv/cs2")
    values.update(overrides)
    return SimpleNamespace(**values)


class _ScriptFile:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self):
        return "#!/bin/bash\necho ok"


@pytest.mark.asyncio
async def test_selfcheck_fixes_steamclient_gameinfo_and_script(monkeypatch):
    manager = SSHManager(use_pool=False)
    server = _server()
    progress = []
    commands = []

    async def execute(command, **_kwargs):
        commands.append(command)
        if "linuxsteamrt64/cs2 &&" in command:
            return True, "exists", ""
        if command.startswith("chmod +x"):
            return False, "", "chmod"
        if "test -L" in command:
            return True, "missing", ""
        if "steamclient.so && echo 'found'" in command:
            return True, "found", ""
        if command.startswith("ln -sf"):
            return True, "", ""
        if "addons/metamod" in command and command.startswith("test -d"):
            return True, "exists", ""
        if command.startswith("test -f") and command.endswith("gameinfo.gi && echo 'exists'"):
            return True, "exists", ""
        if command.startswith("grep -q"):
            return True, "notfound", ""
        if command.startswith("sed -i"):
            return True, "", ""
        if "cs2_autorestart.sh && test -x" in command:
            return False, "", "missing"
        if command.startswith("cat >"):
            return True, "", ""
        return True, "", ""

    manager.execute_command = execute
    monkeypatch.setattr(manager, "disconnect", AsyncMock())
    monkeypatch.setattr(
        "services.ssh.game_selfcheck.anyio.open_file", AsyncMock(return_value=_ScriptFile())
    )

    async def callback(message):
        progress.append(message)

    ok, message = await manager.perform_server_selfcheck(server, callback)
    assert ok and "auto-fixes" in message
    assert "steamclient.so symlink" in " ".join(progress)
    assert any(command.startswith("cat >") for command in commands)


@pytest.mark.asyncio
async def test_selfcheck_unfixed_and_missing_source_paths(monkeypatch):
    manager = SSHManager(use_pool=False)
    server = _server()
    manager.execute_command = AsyncMock(return_value=(True, "missing", ""))
    issues = []
    fixed = []
    logs = AsyncMock()
    await manager._selfcheck_steamclient(server, logs, issues, fixed)
    assert issues == ["steamclient.so symlink missing or broken"] and not fixed

    calls = []

    async def execute(command, **_kwargs):
        calls.append(command)
        if "test -L" in command:
            return True, "missing", ""
        if "steamclient.so && echo 'found'" in command:
            return True, "missing", ""
        if command.startswith("test -f"):
            return False, "", "missing"
        return True, "", ""

    manager.execute_command = execute
    issues = []
    fixed = []
    await manager._selfcheck_steamclient(server, logs, issues, fixed)
    assert issues and not fixed
    assert any("source not found" in str(call) for call in logs.await_args_list)


@pytest.mark.asyncio
async def test_selfcheck_existing_configuration_and_deploy_failure(monkeypatch):
    manager = SSHManager(use_pool=False)
    server = _server()

    async def execute(command, **_kwargs):
        if "linuxsteamrt64/cs2 &&" in command:
            return True, "exists", ""
        if command.startswith("chmod +x"):
            return True, "", ""
        if "test -L" in command:
            return True, "valid", ""
        if command.startswith("test -d"):
            return True, "exists", ""
        if "cs2_autorestart.sh && test -x" in command:
            return False, "", "missing"
        if command.startswith("test -f"):
            return True, "exists", ""
        if command.startswith("grep -q"):
            return True, "found", ""
        if command.startswith("cat >"):
            return False, "", "cannot write"
        return True, "", ""

    manager.execute_command = execute
    monkeypatch.setattr(manager, "disconnect", AsyncMock())
    monkeypatch.setattr(
        "services.ssh.game_selfcheck.anyio.open_file", AsyncMock(return_value=_ScriptFile())
    )
    ok, message = await manager.perform_server_selfcheck(server)
    assert not ok and "remain" in message

    assert await manager._selfcheck_summary(AsyncMock(), [], []) == (
        True,
        "Server self-check passed",
    )
    assert await manager._selfcheck_summary(AsyncMock(), ["one"], ["one"]) == (
        True,
        "Server self-check completed with auto-fixes",
    )
    assert await manager._selfcheck_summary(AsyncMock(), ["one", "two"], ["one"]) == (
        False,
        "1 issues remain",
    )


@pytest.mark.asyncio
async def test_selfcheck_catches_unexpected_exception(monkeypatch):
    manager = SSHManager(use_pool=False)
    monkeypatch.setattr(manager, "execute_command", AsyncMock(side_effect=RuntimeError("broken")))
    ok, message = await manager.perform_server_selfcheck(_server())
    assert not ok and "Self-check error" in message
