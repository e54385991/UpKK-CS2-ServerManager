"""覆盖插件备份的空目录、归档失败、连接失败和尺寸格式化分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh.plugin_backup import PluginBackupMixin


class _Backup(PluginBackupMixin):
    def __init__(self):
        self.connect = AsyncMock(return_value=(True, "connected"))
        self.disconnect = AsyncMock()
        self.execute_command = AsyncMock(return_value=(True, "exists", ""))
        self.execute_command_streaming = AsyncMock(return_value=(True, "", ""))


def _server(**overrides):
    values = SimpleNamespace(game_directory="/srv/cs2")
    for key, value in overrides.items():
        setattr(values, key, value)
    return values


def _async_progress(sink):
    async def callback(message):
        sink.append(message)

    return callback


@pytest.mark.asyncio
async def test_plugin_backup_prepare_and_find_missing_paths():
    manager = _Backup()
    progress = []
    send_progress = _async_progress(progress)
    manager.execute_command = AsyncMock(side_effect=[
        (True, "exists", ""),
        (False, "", ""),
        (False, "", ""),
        (False, "", ""),
    ])
    items = await manager._find_backup_items("/srv/cs2/cs2/game/csgo", send_progress)
    assert items == ["addons"]
    assert any("not found" in item for item in progress)

    manager.execute_command = AsyncMock(return_value=(False, "", ""))
    context, error = await manager._prepare_plugin_backup(_server(), send_progress)
    assert context is None and "not found" in error

    manager.execute_command = AsyncMock(side_effect=[
        (True, "exists", ""),
        (False, "", ""),
    ])
    context, error = await manager._prepare_plugin_backup(_server(), send_progress)
    assert context is None and "Failed to create backups directory" in error

    manager.execute_command = AsyncMock(side_effect=[
        (True, "exists", ""),
        (True, "", ""),
        (False, "", ""),
    ])
    context, error = await manager._prepare_plugin_backup(_server(), send_progress)
    assert context is not None and not error


@pytest.mark.asyncio
async def test_plugin_backup_create_empty_and_archive_failure_paths():
    manager = _Backup()
    progress = []
    send_progress = _async_progress(progress)
    manager._find_backup_items = AsyncMock(return_value=[])
    result = await manager._create_plugin_backup("/csgo", "/backups", "/backups/a.tar.gz", send_progress)
    assert result[0] is False and "No items" in result[1]

    manager._find_backup_items = AsyncMock(return_value=["addons"])
    manager.execute_command = AsyncMock(side_effect=[
        (False, "", ""),
        (True, "tar 1", ""),
        (True, "permissions", ""),
    ])
    manager.execute_command_streaming = AsyncMock(return_value=(False, "", "tar failed"))
    result = await manager._create_plugin_backup("/csgo", "/backups", "/backups/a.tar.gz", send_progress)
    assert result[0] is False and "Backup creation failed" in result[1]

    manager.execute_command = AsyncMock(side_effect=[(True, "exists", "")])
    manager.execute_command_streaming = AsyncMock(return_value=(False, "", "warning"))
    result = await manager._create_plugin_backup("/csgo", "/backups", "/backups/a.tar.gz", send_progress)
    assert result == (True, "", "warning")


@pytest.mark.asyncio
async def test_plugin_backup_finish_sizes_and_top_level_failures():
    manager = _Backup()
    context = ("/srv/cs2", "/srv/cs2/csgo", "/srv/cs2/backups", "x.tar.gz", "/srv/cs2/backups/x.tar.gz", "now")
    progress = []
    send_progress = _async_progress(progress)
    for size in (2**31, 2**21, 2**11, 2):
        manager.execute_command = AsyncMock(return_value=(True, str(size), ""))
        message = await manager._finish_plugin_backup(context, send_progress)
        assert "completed successfully" in message

    manager.connect.return_value = (False, "offline")
    assert await manager.backup_plugins(_server(), progress.append) == (False, "Connection failed: offline")
    manager.connect.return_value = (True, "connected")
    manager.execute_command = AsyncMock(side_effect=RuntimeError("remote error"))
    result = await manager.backup_plugins(_server(), progress.append)
    assert result == (False, "Backup error: remote error")
    assert manager.disconnect.await_count == 1
