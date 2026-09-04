"""覆盖 Metamod/CS2Fixes 面板代理下载和安装后的校验分支。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh_manager import SSHManager


def _server(**overrides):
    values = dict(
        id=8,
        user_id=3,
        game_directory="/srv/cs2",
        use_panel_proxy=True,
        github_proxy="https://proxy.invalid",
        sudo_password=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _manager(responses):
    manager = SSHManager(use_pool=False)
    manager.connect = AsyncMock(return_value=(True, "ok"))
    manager.disconnect = AsyncMock()
    manager.execute_command = AsyncMock(side_effect=responses)
    manager.upload_file_with_progress = AsyncMock(return_value=(True, ""))
    return manager


async def _download(_url, path, *, progress_callback, **_kwargs):
    Path(path).write_bytes(b"x" * 20000)
    await progress_callback(20000, 20000)
    return True, ""


@pytest.mark.asyncio
async def test_metamod_panel_proxy_success_and_upload_failure(monkeypatch, tmp_path):
    import services.ssh.plugin_metamod as module

    server = _server()
    manager = _manager(
        [
            (True, "exists", ""),
            (True, "", ""),
            (True, "", ""),
            (True, "exists", ""),
            (True, "found", ""),
            (True, "", ""),
            (True, "installed", ""),
        ]
    )
    manager._fetch_latest_metamod_url = AsyncMock(return_value=(True, "https://github.com/mm.tar.gz"))
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
    from modules.http_helper import http_helper

    monkeypatch.setattr(http_helper, "download_file", _download)
    result = await manager.install_metamod(server)
    assert result == (True, "Metamod:Source installed successfully")
    manager.upload_file_with_progress.assert_awaited_once()

    manager = _manager([(True, "exists", ""), (True, "", "")])
    manager._fetch_latest_metamod_url = AsyncMock(return_value=(True, "https://github.com/mm.tar.gz"))
    monkeypatch.setattr(http_helper, "download_file", _download)
    manager.upload_file_with_progress = AsyncMock(return_value=(False, "upload failed"))
    result = await manager.install_metamod(server)
    assert not result[0] and "Failed to upload" in result[1]


@pytest.mark.asyncio
async def test_cs2fixes_panel_proxy_success_and_download_failure(monkeypatch, tmp_path):
    import services.ssh.plugin_cs2fixes as module

    server = _server()
    manager = _manager(
        [
            (True, "exists", ""),
            (True, "exists", ""),
            (True, "", ""),
            (True, "", ""),
            (True, "", ""),
            (True, "installed", ""),
        ]
    )
    manager._fetch_github_release_url = AsyncMock(return_value=(True, "https://github.com/fix.tar.gz"))
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
    from modules.http_helper import http_helper

    monkeypatch.setattr(http_helper, "download_file", _download)
    result = await manager.install_cs2fixes(server)
    assert result == (True, "CS2Fixes installed successfully")
    manager.upload_file_with_progress.assert_awaited_once()

    manager = _manager([(True, "exists", ""), (True, "exists", ""), (True, "", "")])
    manager._fetch_github_release_url = AsyncMock(return_value=(True, "https://github.com/fix.tar.gz"))
    monkeypatch.setattr(http_helper, "download_file", AsyncMock(return_value=(False, "cdn")))
    result = await manager.install_cs2fixes(server)
    assert not result[0] and "Failed to download" in result[1]
