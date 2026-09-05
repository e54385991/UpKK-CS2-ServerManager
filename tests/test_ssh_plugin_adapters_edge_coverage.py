"""覆盖插件适配器的失败、回退和更新委托路径，不访问网络或远程主机。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh_manager import SSHManager


def _server(**overrides):
    values = {
        "id": 8,
        "user_id": 3,
        "game_directory": "/srv/cs2",
        "use_panel_proxy": False,
        "github_proxy": "",
        "sudo_password": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _manager(monkeypatch, module, markers=None, **execute):
    manager = SSHManager(use_pool=False)
    manager.connect = AsyncMock(return_value=(True, "ok"))
    manager.disconnect = AsyncMock()
    default = execute.pop("default", (False, "", "remote failure"))
    execute.update(markers or {})

    async def run(command, **_kwargs):
        for marker, result in sorted(execute.items(), key=lambda item: len(item[0]), reverse=True):
            if marker in command:
                return result
        return default

    manager.execute_command = run
    manager.execute_command_streaming = AsyncMock(return_value=(False, "", "download failed"))
    return manager


@pytest.mark.asyncio
async def test_metamod_install_covers_connection_prerequisites_and_download_failures(monkeypatch):
    import services.ssh.plugin_metamod as module

    server = _server()
    manager = _manager(monkeypatch, module)
    manager.connect.return_value = (False, "offline")
    assert await manager.install_metamod(server) == (False, "Connection failed: offline")

    manager = _manager(monkeypatch, module)
    assert await manager.install_metamod(server) == (
        False,
        "CS2 server not found. Please deploy the server first.",
    )

    manager = _manager(
        monkeypatch,
        module,
        default=(True, "exists", ""),
        markers={"test -d /srv/cs2/cs2/game/csgo/addons/metamod": (False, "", "missing")},
    )
    manager._fetch_latest_metamod_url = AsyncMock(return_value=(False, "GitHub unavailable"))
    assert await manager.install_metamod(server) == (False, "GitHub unavailable")

    manager = _manager(
        monkeypatch,
        module,
        default=(True, "exists", ""),
        markers={
            "releases": (True, "https://github.com/mm.tar.gz", ""),
            "test -f": (False, "", "missing"),
        },
    )
    manager._fetch_latest_metamod_url = AsyncMock(return_value=(True, "https://github.com/mm.tar.gz"))
    result = await manager.install_metamod(server)
    assert result[0] is False and "download failed" in result[1].lower()
    manager.disconnect.assert_awaited_once()

    manager = _manager(
        monkeypatch,
        module,
        default=(True, "exists", ""),
        markers={"stat -f%z": (True, "10", ""), "test -f": (True, "exists", "")},
    )
    manager._fetch_latest_metamod_url = AsyncMock(return_value=(True, "https://github.com/mm.tar.gz"))
    result = await manager.install_metamod(server)
    assert "too small" in result[1]

    manager = _manager(
        monkeypatch,
        module,
        default=(True, "exists", ""),
        markers={
            "stat -f%z": (True, "2000", ""),
            "test -f": (True, "exists", ""),
            "tar -xzf": (False, "", "bad tar"),
        },
    )
    manager._fetch_latest_metamod_url = AsyncMock(return_value=(True, "https://github.com/mm.tar.gz"))
    result = await manager.install_metamod(server)
    assert result == (False, "Metamod extraction failed: bad tar")


@pytest.mark.asyncio
async def test_metamod_install_covers_gameinfo_and_verification_branches(monkeypatch):
    import services.ssh.plugin_metamod as module

    server = _server()
    base = {
        "default": (True, "", ""),
        "test -d /srv/cs2/cs2": (True, "exists", ""),
        "test -f /tmp/metamod_install_8/metamod.tar.gz": (True, "exists", ""),
        "stat -f%z": (True, "2000", ""),
        "tar -xzf": (True, "", ""),
        "test -f /srv/cs2/cs2/game/csgo/gameinfo.gi": (False, "", "missing"),
    }
    manager = _manager(monkeypatch, module, markers=base)
    manager._fetch_latest_metamod_url = AsyncMock(return_value=(True, "https://github.com/mm.tar.gz"))
    assert "gameinfo.gi not found" in (await manager.install_metamod(server))[1]

    base["test -f /srv/cs2/cs2/game/csgo/gameinfo.gi"] = (True, "exists", "")
    base["grep -q"] = (True, "found", "")
    base["test -d /srv/cs2/cs2/game/csgo/addons/metamod"] = (False, "", "missing")
    manager = _manager(monkeypatch, module, markers=base)
    manager._fetch_latest_metamod_url = AsyncMock(return_value=(True, "https://github.com/mm.tar.gz"))
    assert "verification failed" in (await manager.install_metamod(server))[1]

    base["grep -q"] = (True, "notfound", "")
    base["grep -qF"] = (False, "", "missing")
    base["sed -i"] = (False, "", "read-only")
    base["test -d /srv/cs2/cs2/game/csgo/addons/metamod"] = (True, "installed", "")
    manager = _manager(monkeypatch, module, markers=base)
    manager._fetch_latest_metamod_url = AsyncMock(return_value=(True, "https://github.com/mm.tar.gz"))
    result = await manager.install_metamod(server)
    assert result == (False, "Metamod gameinfo.gi configuration verification failed")

    manager = SSHManager(use_pool=False)
    manager.install_metamod = AsyncMock(return_value=(True, "updated"))
    assert await manager.update_metamod(server) == (True, "updated")


@pytest.mark.asyncio
async def test_cs2fixes_install_covers_prerequisites_fetch_and_download_errors(monkeypatch):
    import services.ssh.plugin_cs2fixes as module

    server = _server()
    manager = _manager(monkeypatch, module)
    manager.connect.return_value = (False, "offline")
    assert await manager.install_cs2fixes(server) == (False, "Connection failed: offline")
    manager = _manager(monkeypatch, module)
    assert "CS2 server not found" in (await manager.install_cs2fixes(server))[1]

    manager = _manager(
        monkeypatch,
        module,
        default=(True, "exists", ""),
        markers={"test -d /srv/cs2/cs2/game/csgo/addons/metamod": (False, "", "missing")},
    )
    manager.install_metamod = AsyncMock(return_value=(False, "mm failed"))
    assert await manager.install_cs2fixes(server) == (False, "Metamod installation failed: mm failed")

    manager = _manager(
        monkeypatch,
        module,
        default=(True, "exists", ""),
        markers={"test -d /srv/cs2/cs2/game/csgo/addons/metamod": (False, "", "missing")},
    )
    manager.install_metamod = AsyncMock(return_value=(True, "ok"))
    manager._fetch_github_release_url = AsyncMock(return_value=(False, "release missing"))
    assert await manager.install_cs2fixes(server) == (False, "release missing")

    manager = _manager(
        monkeypatch,
        module,
        default=(True, "exists", ""),
        markers={
            "test -d /srv/cs2/cs2/game/csgo/addons/metamod": (True, "exists", ""),
            "test -f": (False, "", "missing"),
        },
    )
    manager._fetch_github_release_url = AsyncMock(return_value=(True, "https://github.com/fix.tar.gz"))
    assert "download failed" in (await manager.install_cs2fixes(server))[1].lower()

    manager = _manager(
        monkeypatch,
        module,
        default=(True, "exists", ""),
        markers={
            "test -d /srv/cs2/cs2/game/csgo/addons/metamod": (True, "exists", ""),
            "test -f": (True, "exists", ""),
            "stat -f%z": (True, "10", ""),
        },
    )
    manager._fetch_github_release_url = AsyncMock(return_value=(True, "https://github.com/fix.tar.gz"))
    assert "too small" in (await manager.install_cs2fixes(server))[1]

    manager = SSHManager(use_pool=False)
    manager.install_cs2fixes = AsyncMock(return_value=(False, "not installed"))
    assert await manager.update_cs2fixes(server) == (False, "not installed")


@pytest.mark.asyncio
async def test_swiftly_install_covers_release_fallback_and_download_errors(monkeypatch):
    import services.ssh.plugin_swiftly as module

    server = _server()
    manager = _manager(monkeypatch, module)
    manager.connect.return_value = (False, "offline")
    assert await manager.install_swiftly(server) == (False, "Connection failed: offline")
    manager = _manager(monkeypatch, module)
    assert "CS2 server not found" in (await manager.install_swiftly(server))[1]

    manager = _manager(monkeypatch, module, default=(True, "exists", ""))
    manager.execute_command = AsyncMock(side_effect=[
        (True, "exists", ""),
        (False, "", "api error"),
        (False, "", "still down"),
    ])
    assert "Could not determine" in (await manager.install_swiftly(server))[1]

    manager = _manager(
        monkeypatch,
        module,
        default=(True, "exists", ""),
        markers={"test -f": (False, "", "missing")},
    )
    manager.execute_command_streaming = AsyncMock(return_value=(False, "", "curl failed"))
    manager.execute_command = AsyncMock(side_effect=[
        (True, "exists", ""),
        (True, "https://github.com/swiftly.zip", ""),
        (True, "", ""),
        (False, "", "missing"),
        (True, "", ""),
    ])
    result = await manager.install_swiftly(server)
    assert "download failed" in result[1].lower()

    manager = SSHManager(use_pool=False)
    manager.install_swiftly = AsyncMock(return_value=(True, "updated"))
    assert await manager.update_swiftly(server) == (True, "updated")


@pytest.mark.asyncio
async def test_counterstrikesharp_install_success_proxy_and_unzip_paths(monkeypatch, tmp_path):
    import services.ssh.plugin_counterstrikesharp as module

    server = _server(github_proxy="https://proxy.invalid", use_panel_proxy=False)
    manager = _manager(monkeypatch, module)
    manager.execute_command = AsyncMock(
        side_effect=[
            (True, "exists", ""),  # CS2
            (True, "exists", ""),  # Metamod
            (True, "https://github.com/css.zip", ""),  # API
            (True, "", ""),  # mkdir
            (True, "exists", ""),  # downloaded file
            (True, "20000", ""),  # size
            (True, "", ""),  # unzip available
            (True, "", ""),  # extract
            (True, "extracted", ""),
            (True, "", ""),  # cleanup
            (True, "installed", ""),
        ]
    )
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
    progress = []
    result = await manager.install_counterstrikesharp(server, progress.append)
    assert result == (True, "CounterStrikeSharp installed successfully")
    assert any("GitHub proxy" in message for message in progress)
    manager.disconnect.assert_awaited_once()

    server = _server(use_panel_proxy=True)
    manager = _manager(monkeypatch, module)
    manager.execute_command = AsyncMock(
        side_effect=[
            (True, "exists", ""),
            (True, "exists", ""),
            (True, "https://github.com/css.zip", ""),
            (True, "", ""),
            (True, "", ""),
            (True, "", ""),
            (True, "extracted", ""),
            (True, "", ""),
            (True, "installed", ""),
        ]
    )

    async def download(_url, path, **_kwargs):
        Path(path).write_bytes(b"x" * 10001)
        return True, ""

    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
    from modules.http_helper import http_helper

    monkeypatch.setattr(http_helper, "download_file", download)
    manager.upload_file_with_progress = AsyncMock(return_value=(True, ""))
    assert await manager.install_counterstrikesharp(server) == (
        True,
        "CounterStrikeSharp installed successfully",
    )
    manager.upload_file_with_progress.assert_awaited_once()


@pytest.mark.asyncio
async def test_counterstrikesharp_fallback_and_package_installation(monkeypatch):
    import services.ssh.plugin_counterstrikesharp as module

    server = _server(sudo_password="pw")
    manager = _manager(monkeypatch, module)
    manager.install_metamod = AsyncMock(return_value=(True, "installed"))
    manager.execute_command = AsyncMock(
        side_effect=[
            (True, "exists", ""),  # CS2
            (False, "", "missing"),  # Metamod
            (False, "", "api"),  # preferred API
            (False, "", "api"),  # alternate API
            (True, "v1.2.3", ""),  # tag fallback
            (True, "", ""),  # mkdir
            (True, "exists", ""),  # downloaded file
            (True, "20000", ""),
            (False, "", "no unzip"),
            (True, "apt", ""),
            (False, "", "apt failed"),
            (True, "", ""),  # sudo install
            (True, "", ""),  # unzip recheck
            (True, "", ""),  # extract
            (True, "extracted", ""),
            (True, "", ""),
            (True, "installed", ""),
        ]
    )
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
    result = await manager.install_counterstrikesharp(server)
    assert result == (True, "CounterStrikeSharp installed successfully")
    assert "counterstrikesharp-with-runtime-linux-1.2.3.zip" in manager.execute_command_streaming.await_args.args[0]


@pytest.mark.asyncio
async def test_swiftly_install_success_copies_nested_addons(monkeypatch):
    import services.ssh.plugin_swiftly as module

    server = _server(github_proxy="https://proxy.invalid")
    manager = _manager(monkeypatch, module)
    manager.execute_command = AsyncMock(
        side_effect=[
            (True, "exists", ""),  # CS2
            (True, "https://github.com/swiftly.zip", ""),
            (True, "", ""),  # mkdir
            (True, "exists", ""),  # downloaded file
            (True, "20000", ""),
            (True, "", ""),  # unzip available
            (True, "", ""),  # extract
            (True, "/tmp/swiftly_install_8/extracted/release/addons", ""),
            (True, "", ""),  # copy
            (True, "extracted", ""),
            (True, "", ""),
            (True, "installed", ""),
        ]
    )
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
    result = await manager.install_swiftly(server)
    assert result == (True, "SwiftlyS2 installed successfully")
    download_command = manager.execute_command_streaming.await_args.args[0]
    assert "proxy.invalid" in download_command
