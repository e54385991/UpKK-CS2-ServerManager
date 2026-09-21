"""覆盖 SwiftlyS2 下载回退、依赖安装和解压校验分支。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh_manager import SSHManager
from tests.gameinfo_samples import GAMEINFO_BOTH, GAMEINFO_METAMOD, GAMEINFO_SWIFTLY


def _server(**overrides):
    values = dict(
        id=8,
        user_id=3,
        game_directory="/srv/cs2",
        use_panel_proxy=False,
        github_proxy="",
        sudo_password=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _manager(monkeypatch, *, responses, server=None):
    import services.ssh.plugin_swiftly as module

    manager = SSHManager(use_pool=False)
    manager.connect = AsyncMock(return_value=(True, "ok"))
    manager.disconnect = AsyncMock()
    manager.execute_command = AsyncMock(side_effect=responses)
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
    return manager, module, server or _server()


def _responses(*, package="unzip", extract=True, addons=True, copied=True, installed=True):
    responses = [
        (True, "exists", ""),
        (False, "", "primary api"),
        (True, "https://github.com/swiftly.zip", ""),
        (True, "", ""),
        (True, "exists", ""),
        (True, "20000", ""),
        (package == "unzip", "/usr/bin/unzip" if package == "unzip" else "", ""),
    ]
    if package != "unzip":
        responses.append((True, "apt" if package == "apt" else "none", ""))
    if package == "apt":
        responses.extend([(True, "", ""), (True, "", "")])
    responses.extend(
        [
            (True, "", "") if extract else (False, "", "bad zip"),
            (True, "/tmp/swiftly_install_8/extracted/release/addons", "")
            if addons
            else (True, "", ""),
            (True, "found", "") if addons else (True, "", ""),
            (True, "", "") if copied else (False, "", "copy failed"),
            (True, "extracted", "") if addons else (False, "", "missing"),
            (True, "", "") if addons else (True, "", ""),
            (True, "installed", "") if installed else (False, "", "missing"),
        ]
    )
    if addons and installed:
        responses.extend(
            [
                (True, "exists", ""),
                (True, "", ""),
                (True, GAMEINFO_SWIFTLY, ""),
            ]
        )
    return responses


@pytest.mark.asyncio
async def test_swiftly_package_manager_missing_and_sudo_failure(monkeypatch):
    manager, _module, server = _manager(monkeypatch, responses=_responses(package="none"))
    result = await manager.install_swiftly(server)
    assert result == (
        False,
        "unzip not found and package manager not detected. Please install unzip manually.",
    )

    manager, _module, server = _manager(monkeypatch, responses=_responses(package="apt"))
    # command sequence reaches apt install: make its first install fail and
    # keep the server without sudo credentials.
    manager.execute_command.side_effect = [
        (True, "exists", ""),
        (False, "", "primary"),
        (True, "url", ""),
        (True, "", ""),
        (True, "exists", ""),
        (True, "20000", ""),
        (False, "", "no unzip"),
        (True, "apt", ""),
        (False, "", "apt failed"),
        (True, "", ""),
    ]
    result = await manager.install_swiftly(server)
    assert not result[0] and "no sudo password" in result[1]


@pytest.mark.asyncio
async def test_swiftly_fallback_sudo_install_recheck_and_extract_errors(monkeypatch):
    manager, _module, server = _manager(monkeypatch, responses=())
    server.sudo_password = "sudo-pass"
    manager.execute_command.side_effect = [
        (True, "exists", ""),
        (False, "", "primary"),
        (True, "url", ""),
        (True, "", ""),
        (True, "exists", ""),
        (True, "20000", ""),
        (False, "", "no unzip"),
        (True, "apt", ""),
        (False, "", "apt failed"),
        (False, "", "sudo failed"),
        (True, "", ""),
    ]
    result = await manager.install_swiftly(server)
    assert not result[0] and "Could not install unzip" in result[1]

    manager, _module, server = _manager(monkeypatch, responses=())
    server.sudo_password = "sudo-pass"
    manager.execute_command.side_effect = [
        (True, "exists", ""),
        (False, "", "primary"),
        (True, "url", ""),
        (True, "", ""),
        (True, "exists", ""),
        (True, "20000", ""),
        (False, "", "no unzip"),
        (True, "apt", ""),
        (False, "", "apt failed"),
        (True, "", ""),
        (False, "", "still missing"),
        (True, "", ""),
    ]
    result = await manager.install_swiftly(server)
    assert not result[0] and "still not found" in result[1]

    manager, _module, server = _manager(monkeypatch, responses=_responses(extract=False))
    result = await manager.install_swiftly(server)
    assert not result[0] and "extraction failed" in result[1]


@pytest.mark.asyncio
async def test_swiftly_rejects_archive_without_loader_tree(monkeypatch):
    manager, _module, server = _manager(monkeypatch, responses=())
    manager.execute_command.side_effect = [
        (True, "exists", ""),
        (False, "", "primary"),
        (True, "url", ""),
        (True, "", ""),
        (True, "exists", ""),
        (True, "20000", ""),
        (True, "/usr/bin/unzip", ""),
        (True, "", ""),
        (True, "/tmp/swiftly_install_8/extracted/release/addons", ""),
        (False, "", "missing loader"),
        (True, "", ""),
    ]
    result = await manager.install_swiftly(server)
    assert not result[0] and "addons/swiftlys2 not found in archive" in result[1]


@pytest.mark.asyncio
async def test_swiftly_addons_copy_and_install_verification_failures(monkeypatch):
    for addons, copied, installed, expected in (
        (False, True, True, "addons' directory not found"),
        (True, False, False, "installation verification failed"),
        (True, True, False, "installation verification failed"),
    ):
        manager, _module, server = _manager(
            monkeypatch,
            responses=_responses(addons=addons, copied=copied, installed=installed),
        )
        result = await manager.install_swiftly(server)
        assert not result[0] and expected in result[1]


@pytest.mark.asyncio
async def test_swiftly_primary_api_empty_then_alternate_empty(monkeypatch):
    manager, _module, server = _manager(monkeypatch, responses=())
    manager.execute_command.side_effect = [
        (True, "exists", ""),
        (False, "", "primary"),
        (False, "", "alternate"),
    ]
    result = await manager.install_swiftly(server)
    assert result == (False, "Could not determine SwiftlyS2 download URL from GitHub API")


@pytest.mark.asyncio
async def test_swiftly_panel_proxy_download_upload_and_cleanup(monkeypatch, tmp_path):
    manager, module, server = _manager(monkeypatch, responses=())
    server.use_panel_proxy = True
    manager.execute_command.side_effect = [
        (True, "exists", ""),
        (True, "https://github.com/swiftly.zip", ""),
        (True, "", ""),
        (True, "", ""),
        (True, "", ""),
        (True, "/tmp/swiftly_install_8/extracted/release/addons", ""),
        (True, "found", ""),
        (True, "", ""),
        (True, "extracted", ""),
        (True, "", ""),
        (True, "installed", ""),
        (True, "exists", ""),
        (True, "", ""),
        (True, GAMEINFO_SWIFTLY, ""),
    ]
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
    from modules.http_helper import http_helper

    async def download(_url, path, *, progress_callback, **_kwargs):
        Path(path).write_bytes(b"x" * 10001)
        await progress_callback(10000, 10000)
        return True, ""

    monkeypatch.setattr(http_helper, "download_file", download)
    manager.upload_file_with_progress = AsyncMock(return_value=(True, ""))
    result = await manager.install_swiftly(server)
    assert result == (True, "SwiftlyS2 installed successfully")
    manager.upload_file_with_progress.assert_awaited_once()


@pytest.mark.asyncio
async def test_swiftly_panel_proxy_download_and_upload_failures(monkeypatch, tmp_path):
    manager, module, server = _manager(monkeypatch, responses=())
    server.use_panel_proxy = True
    manager.execute_command.side_effect = [
        (True, "exists", ""),
        (True, "https://github.com/swiftly.zip", ""),
        (True, "", ""),
    ]
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
    from modules.http_helper import http_helper

    monkeypatch.setattr(http_helper, "download_file", AsyncMock(return_value=(False, "cdn")))
    result = await manager.install_swiftly(server)
    assert not result[0] and "Failed to download" in result[1]

    manager, module, server = _manager(monkeypatch, responses=())
    server.use_panel_proxy = True
    manager.execute_command.side_effect = [
        (True, "exists", ""),
        (True, "https://github.com/swiftly.zip", ""),
        (True, "", ""),
    ]
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))

    async def tiny_download(_url, path, **_kwargs):
        Path(path).write_bytes(b"tiny")
        return True, ""

    monkeypatch.setattr(http_helper, "download_file", tiny_download)
    result = await manager.install_swiftly(server)
    assert not result[0] and "too small" in result[1]

    manager, module, server = _manager(monkeypatch, responses=())
    server.use_panel_proxy = True
    manager.execute_command.side_effect = [
        (True, "exists", ""),
        (True, "https://github.com/swiftly.zip", ""),
        (True, "", ""),
    ]
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))

    async def good_download(_url, path, **_kwargs):
        Path(path).write_bytes(b"x" * 10001)
        return True, ""

    monkeypatch.setattr(http_helper, "download_file", good_download)
    manager.upload_file_with_progress = AsyncMock(return_value=(False, "upload"))
    result = await manager.install_swiftly(server)
    assert not result[0] and "Failed to upload" in result[1]


@pytest.mark.asyncio
async def test_swiftly_install_keeps_existing_metamod_first_in_gameinfo(monkeypatch):
    import base64

    manager, _module, server = _manager(monkeypatch, responses=())
    written: list[str] = []
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))

    async def execute(command: str, **_kwargs):
        if command.startswith("test -d") and command.endswith("cs2 && echo 'exists'"):
            return True, "exists", ""
        if "releases/latest" in command:
            return True, "https://github.com/swiftly.zip", ""
        if command.startswith("mkdir -p /tmp/swiftly_install_8"):
            return True, "", ""
        if command.startswith("test -f") and "swiftly.zip" in command:
            return True, "exists", ""
        if command.startswith("stat "):
            return True, "20000", ""
        if command == "command -v unzip":
            return True, "/usr/bin/unzip", ""
        if command.startswith("mkdir -p") and "extracted" in command:
            return True, "", ""
        if "find " in command and "-name 'addons'" in command:
            return True, "/tmp/swiftly_install_8/extracted/release/addons", ""
        if "echo 'found'" in command:
            return True, "found", ""
        if "cp -rf" in command:
            assert "/addons/swiftlys2" in command
            assert "/*" not in command
            return True, "", ""
        if "echo 'extracted'" in command or "echo 'installed'" in command:
            return True, "extracted" if "extracted" in command else "installed", ""
        if command.startswith("rm -rf"):
            return True, "", ""
        if command.startswith("test -f") and "gameinfo.gi" in command:
            return True, "exists", ""
        if command.startswith("test -d") and "addons/metamod" in command:
            return True, "exists", ""
        if command.startswith("cat ") and "gameinfo.gi" in command:
            return True, GAMEINFO_METAMOD, ""
        if command.startswith("cp ") and "gameinfo.gi" in command:
            return True, "", ""
        if "base64 -d" in command:
            payload = command.split("printf '%s' ", 1)[1].split(" | base64", 1)[0].strip("'")
            written.append(base64.b64decode(payload).decode())
            return True, "", ""
        return True, "", ""

    manager.execute_command = execute
    result = await manager.install_swiftly(server)
    assert result == (True, "SwiftlyS2 installed successfully")
    assert written == [GAMEINFO_BOTH]
