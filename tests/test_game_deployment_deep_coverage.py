"""补齐 CS2 部署的依赖、下载、重试和清理分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh.game_deployment import GameDeploymentMixin


def _server(**overrides):
    values = {
        "id": 5,
        "user_id": 2,
        "game_directory": "/srv/cs2",
        "ssh_user": "cs2",
        "session_manager": "tmux",
        "use_panel_proxy": False,
        "sudo_password": None,
        "ssh_password": None,
        "apt_mirror": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Deployment(GameDeploymentMixin):
    def __init__(self):
        self.current_server = None
        self.connect = AsyncMock(return_value=(True, "connected"))
        self.disconnect = AsyncMock()
        self.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
        self.execute_sudo_command = AsyncMock(return_value=(False, "", "sudo failed"))


@pytest.mark.asyncio
async def test_deployment_preflight_progress_and_home_permission_paths(monkeypatch):
    manager = _Deployment()
    result = SimpleNamespace(success=True, message="ready", apt_mirror=None)
    monkeypatch.setattr("services.ssh.game_deployment.ensure_steamcmd_packages", AsyncMock(return_value=result))
    manager.current_server = _server()
    progress = []
    assert await manager._steamcmd_host_preflight_connected(progress.append) == (True, "ready")
    assert progress == []  # sync callback is called by the dependency fake only when it emits

    server = _server(game_directory="/home/cs2server/game")
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "runtime ready"))

    async def missing_user(command, **_kwargs):
        if command.startswith("id cs2server"):
            return True, "missing", ""
        return True, "", ""

    manager.execute_command = missing_user
    assert "Environment not initialized" in (await manager.deploy_cs2_server(server))[1]

    server = _server(game_directory="/home/cs2server/game", ssh_password="pw")

    async def cannot_write(command, **_kwargs):
        if command.startswith("id cs2server"):
            return True, "exists", ""
        if command.startswith("test -w"):
            return False, "not_writable", ""
        if command.startswith("echo 'pw'"):
            return False, "", "denied"
        return True, "", ""

    manager.execute_command = cannot_write
    result = await manager.deploy_cs2_server(server)
    assert result[0] is False and "correct permissions" in result[1]

    server = _server(game_directory="/home/cs2server/game")
    manager.execute_command = cannot_write
    result = await manager.deploy_cs2_server(server)
    assert result[0] is False and "Permission denied" in result[1]


@pytest.mark.asyncio
async def test_deployment_preflight_supports_async_progress_and_updates_mirror(monkeypatch):
    manager = _Deployment()
    manager.current_server = _server(apt_mirror="old")
    progress = []

    async def ensure(_runner, _packages, *, progress, **_kwargs):
        await progress("dependency progress")
        return SimpleNamespace(success=True, message="ready", apt_mirror="new")

    monkeypatch.setattr("services.ssh.game_deployment.ensure_steamcmd_packages", ensure)

    async def callback(message):
        progress.append(message)

    assert await manager._steamcmd_host_preflight_connected(callback) == (True, "ready")
    assert progress == ["dependency progress"]
    assert manager.current_server.apt_mirror == "new"


@pytest.mark.asyncio
async def test_deployment_tool_and_directory_failures(monkeypatch):
    manager = _Deployment()
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "ready"))
    server = _server()

    async def no_package_manager(command, **_kwargs):
        if command.startswith("command -v"):
            return False, "", "missing"
        if "apt-get" in command and "command -v" in command:
            return True, "none", ""
        return True, "", ""

    manager.execute_command = no_package_manager
    result = await manager.deploy_cs2_server(server)
    assert result[0] is False and "still missing" in result[1]

    async def game_dir_failure(command, **_kwargs):
        if command == "mkdir -p /srv/cs2":
            return False, "", "read-only"
        return True, "/usr/bin/tool", ""

    manager.execute_command = game_dir_failure
    result = await manager.deploy_cs2_server(server)
    assert result == (False, "Directory creation failed: read-only")

    async def steamcmd_dir_failure(command, **_kwargs):
        if command == "mkdir -p /srv/cs2/steamcmd":
            return False, "", "cannot create"
        return True, "/usr/bin/tool", ""

    manager.execute_command = steamcmd_dir_failure
    result = await manager.deploy_cs2_server(server)
    assert result == (False, "SteamCMD directory creation failed: cannot create")


@pytest.mark.asyncio
async def test_deployment_download_extraction_and_steamcmd_terminal_paths(monkeypatch):
    server = _server()
    manager = _Deployment()
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "ready"))

    async def all_success(command, **_kwargs):
        return True, "exists", ""

    async def download_missing(command, **_kwargs):
        if command.startswith("test -f"):
            return False, "", "missing"
        return await all_success(command, **_kwargs)

    manager.execute_command = download_missing
    manager.execute_command_streaming.return_value = (False, "", "wget failed")
    manager._execute_steamcmd_with_retry = AsyncMock(return_value=(True, "", ""))
    result = await manager.deploy_cs2_server(server)
    assert result[0] is False and "download failed" in result[1]

    async def file_exists(command, **_kwargs):
        if command.startswith("tar -xzf"):
            return False, "", "bad archive"
        return True, "exists", ""

    manager = _Deployment()
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "ready"))
    manager.execute_command = file_exists
    manager.execute_command_streaming.return_value = (False, "", "wget failed")
    result = await manager.deploy_cs2_server(server)
    assert result == (False, "SteamCMD extraction failed: bad archive")

    manager = _Deployment()
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "ready"))
    manager.execute_command = all_success
    manager._execute_steamcmd_with_retry = AsyncMock(return_value=(False, "", "network"))
    manager._cs2_executable_path = lambda _server: "/srv/cs2/cs2/game/bin/cs2"
    monkeypatch.setattr("services.ssh.game_deployment.resolve_steamcmd_max_retries", AsyncMock(return_value=2))
    result = await manager.deploy_cs2_server(server)
    assert result[0] is False and "network" in result[1]

    calls = []

    async def symlink_failure(command, **_kwargs):
        calls.append(command)
        if command.startswith("ln -sf"):
            return False, "", "link failed"
        return True, "exists", ""

    manager = _Deployment()
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "ready"))
    manager.execute_command = symlink_failure
    manager._execute_steamcmd_with_retry = AsyncMock(return_value=(True, "", ""))
    manager._cs2_executable_exists_connected = AsyncMock(return_value=(True, "/srv/cs2/cs2"))
    monkeypatch.setattr("services.ssh.game_deployment.resolve_steamcmd_max_retries", AsyncMock(return_value=1))
    result = await manager.deploy_cs2_server(server)
    assert result == (True, "CS2 server deployed successfully")
    assert any(command.startswith("ln -sf") for command in calls)


@pytest.mark.asyncio
async def test_deployment_panel_proxy_download_upload_and_cleanup(monkeypatch, tmp_path):
    server = _server(use_panel_proxy=True)
    manager = _Deployment()
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "ready"))
    manager._execute_steamcmd_with_retry = AsyncMock(return_value=(True, "", ""))
    manager._cs2_executable_exists_connected = AsyncMock(return_value=(True, "/srv/cs2/cs2"))
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    monkeypatch.setattr("services.ssh.game_deployment.resolve_steamcmd_max_retries", AsyncMock(return_value=1))
    monkeypatch.setattr("services.ssh.game_deployment.tempfile.gettempdir", lambda: str(tmp_path))

    async def download(_url, path, *, progress_callback, **_kwargs):
        with open(path, "wb") as output:
            output.write(b"x" * 2000)
        await progress_callback(0, 2000)
        await progress_callback(2000, 2000)
        return True, ""

    async def upload(_source, _target, _server, *, progress_callback):
        await progress_callback(0, 2000)
        await progress_callback(2000, 2000)
        return True, ""

    monkeypatch.setattr("modules.http_helper.http_helper.download_file", download)
    manager.upload_file_with_progress = upload

    result = await manager.deploy_cs2_server(server)
    assert result == (True, "CS2 server deployed successfully")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("download_result", "upload_result", "expected"),
    [
        ((False, "network"), None, "Failed to download SteamCMD: network"),
        ((True, ""), (False, "remote disk full"), "Failed to upload SteamCMD: remote disk full"),
    ],
)
async def test_deployment_panel_proxy_failure_paths(
    monkeypatch, tmp_path, download_result, upload_result, expected
):
    server = _server(use_panel_proxy=True)
    manager = _Deployment()
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "ready"))
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    monkeypatch.setattr("services.ssh.game_deployment.tempfile.gettempdir", lambda: str(tmp_path))

    async def download(_url, path, **_kwargs):
        if download_result[0]:
            with open(path, "wb") as output:
                output.write(b"x" * 2000)
        return download_result

    monkeypatch.setattr("modules.http_helper.http_helper.download_file", download)
    if upload_result is not None:
        manager.upload_file_with_progress = AsyncMock(return_value=upload_result)

    result = await manager.deploy_cs2_server(server)
    assert result == (False, f"Deployment error: {expected}")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_deployment_panel_proxy_rejects_tiny_download(monkeypatch, tmp_path):
    server = _server(use_panel_proxy=True)
    manager = _Deployment()
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "ready"))
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    monkeypatch.setattr("services.ssh.game_deployment.tempfile.gettempdir", lambda: str(tmp_path))

    async def download(_url, path, **_kwargs):
        with open(path, "wb") as output:
            output.write(b"tiny")
        return True, ""

    monkeypatch.setattr("modules.http_helper.http_helper.download_file", download)
    result = await manager.deploy_cs2_server(server)
    assert result == (
        False,
        "Deployment error: Downloaded SteamCMD file is too small or empty",
    )
    assert list(tmp_path.iterdir()) == []
