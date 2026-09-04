"""覆盖远程下载、归档预检和 CS2 部署的安全短路路径。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh.file_download_extract import DownloadExtractMixin
from services.ssh.game_deployment import GameDeploymentMixin
from services.ssh.game_steamcmd import GameSteamcmdMixin


def _server(**overrides):
    values = dict(
        game_directory="/srv/cs2", id=3, user_id=7, ssh_user="cs2", session_manager="tmux"
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_remote_download_short_circuits_before_network():
    manager = DownloadExtractMixin()
    server = _server()
    manager.archive_type_from_path = lambda path: "zip" if path.endswith(".zip") else None
    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    manager._find_remote_tool = AsyncMock(return_value=None)
    assert await manager.download_url_to_file("https://x/a.zip", "/srv/cs2/a.txt", server) == (
        False,
        "Target filename does not use a supported archive extension",
    )
    assert await manager.download_url_to_file("https://x/a.zip", None, server) == (
        False,
        "Download destination path is required",
    )
    manager.validate_path_within_base.return_value = (False, "outside")
    assert await manager.download_url_to_file("https://x/a.zip", "/srv/cs2/a.zip", server) == (
        False,
        "outside",
    )
    manager.validate_path_within_base.return_value = (True, "")
    assert await manager.download_url_to_file("https://x/a.zip", "/srv/cs2/a.zip", server) == (
        False,
        "Required download tool is missing: install curl",
    )
    manager._find_remote_tool.side_effect = ["/usr/bin/curl", None]
    assert await manager.download_url_to_file("https://x/a.zip", "/srv/cs2/a.zip", server) == (
        False,
        "Required DNS resolver is missing: install getent",
    )


@pytest.mark.asyncio
async def test_archive_extract_validation_short_circuits(monkeypatch):
    manager = DownloadExtractMixin()
    server = _server()
    manager.archive_type_from_path = lambda path: "zip" if path.endswith(".zip") else None
    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    manager._inspect_archive_connected = AsyncMock(return_value=(False, {}, "invalid archive"))
    assert (await manager.extract_archive("/srv/cs2/file.unknown", "/srv/cs2/out", server))[
        0
    ] is False
    manager.archive_type_from_path = lambda _path: "zip"
    manager.validate_path_within_base.return_value = (False, "archive outside")
    assert await manager.extract_archive("/srv/cs2/a.zip", "/srv/cs2/out", server) == (
        False,
        "archive outside",
    )
    manager.validate_path_within_base.side_effect = [(True, ""), (False, "destination outside")]
    assert await manager.extract_archive("/srv/cs2/a.zip", "/etc/out", server) == (
        False,
        "destination outside",
    )
    manager.validate_path_within_base.side_effect = None
    manager.validate_path_within_base.return_value = (True, "")
    assert await manager.extract_archive("/srv/cs2/a.zip", "/srv/cs2/out", server) == (
        False,
        "invalid archive",
    )
    manager._inspect_archive_connected.return_value = (
        True,
        {"folders": ["root"], "has_backslash_separators": False},
        "",
    )
    manager._normalize_archive_member = lambda *_args, **_kwargs: (None, "bad source")
    assert await manager.extract_archive(
        "/srv/cs2/a.zip", "/srv/cs2/out", server, source_folder="../root"
    ) == (False, "bad source")
    manager._normalize_archive_member = lambda *_args, **_kwargs: ("missing", None)
    assert (
        "not found"
        in (
            await manager.extract_archive(
                "/srv/cs2/a.zip", "/srv/cs2/out", server, source_folder="missing"
            )
        )[1]
    )


class _Deployment(GameDeploymentMixin):
    def __init__(self):
        self.current_server = None
        self.disconnect = AsyncMock()
        self.connect = AsyncMock(return_value=(True, ""))
        self.execute_command = AsyncMock(return_value=(True, "", ""))


@pytest.mark.asyncio
async def test_deployment_preflight_and_short_failures(monkeypatch):
    manager = _Deployment()
    assert await manager._steamcmd_host_preflight_connected() == (
        False,
        "Not connected to a server host",
    )
    server = _server()
    manager.current_server = server
    result = SimpleNamespace(success=True, message="packages ready", apt_mirror="mirror")
    monkeypatch.setattr(
        "services.ssh.game_deployment.ensure_steamcmd_packages", AsyncMock(return_value=result)
    )
    assert await manager._steamcmd_host_preflight_connected() == (True, "packages ready")
    assert server.apt_mirror == "mirror"

    manager.connect.return_value = (False, "offline")
    assert await manager.deploy_cs2_server(server) == (False, "Connection failed: offline")
    manager.connect.return_value = (True, "ok")
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(False, "missing runtime"))
    success, message = await manager.deploy_cs2_server(server)
    assert not success and message == "missing runtime"
    assert manager.disconnect.await_count >= 1

    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "ready"))
    home_server = _server(game_directory="/home/cs2server/game")
    manager.execute_command = AsyncMock(return_value=(True, "missing", ""))
    success, message = await manager.deploy_cs2_server(home_server)
    assert not success and "Environment not initialized" in message


@pytest.mark.asyncio
async def test_deployment_success_runs_remote_install_and_verification(monkeypatch):
    manager = _Deployment()
    server = _server(
        id=4,
        user_id=7,
        ssh_user="steam",
        use_panel_proxy=False,
        session_manager="tmux",
        sudo_password=None,
        ssh_password="pw",
        apt_mirror="official",
    )
    manager.connect = AsyncMock(return_value=(True, "connected"))
    manager._steamcmd_host_preflight_connected = AsyncMock(return_value=(True, "runtime ready"))
    manager.execute_command_streaming = AsyncMock(return_value=(True, "done", ""))
    manager._cs2_executable_exists_connected = AsyncMock(
        return_value=(True, "/srv/cs2/cs2/game/bin/linuxsteamrt64/cs2")
    )

    async def retry(_command, _server, *, completion_check, **_kwargs):
        assert await completion_check()
        return True, "installed", ""

    manager._execute_steamcmd_with_retry = retry
    manager.execute_command = AsyncMock(return_value=(True, "/usr/bin/tool", ""))
    monkeypatch.setattr(
        "services.ssh.game_deployment.resolve_steamcmd_max_retries",
        AsyncMock(return_value=1),
    )
    success, message = await manager.deploy_cs2_server(server)
    assert (success, message) == (True, "CS2 server deployed successfully")
    assert manager.disconnect.await_count == 1
    assert manager.execute_command_streaming.await_count == 1


@pytest.mark.asyncio
async def test_steamcmd_retry_recovers_and_reports_verified_completion(monkeypatch):
    manager = GameSteamcmdMixin()
    manager.STEAMCMD_RETRY_DELAY = 0
    manager._prepare_steamcmd_retry = AsyncMock(side_effect=[True, True])
    manager._run_steamcmd_attempt = AsyncMock(
        side_effect=[
            (False, "partial", "network", "network", True),
            (True, "complete", "", "", False),
        ]
    )
    progress: list[str] = []
    monkeypatch.setattr("services.ssh.game_steamcmd._legacy_cancel_requested", AsyncMock(return_value=False))

    checks = iter((False, True))

    async def verify():
        return next(checks)

    result = await manager._execute_steamcmd_with_retry(
        "steamcmd",
        _server(id=3),
        progress_callback=progress.append,
        max_retries=1,
        completion_check=verify,
    )
    assert result == (True, "complete", "")
    assert any("auto-recovering" in item for item in progress)
    assert any("retry attempt" in item for item in progress)


@pytest.mark.asyncio
async def test_steamcmd_retry_covers_cancel_and_non_retryable_failures(monkeypatch):
    manager = GameSteamcmdMixin()
    progress = AsyncMock()
    monkeypatch.setattr("services.ssh.game_steamcmd._legacy_cancel_requested", AsyncMock(return_value=True))
    cancelled = await manager._execute_steamcmd_with_retry(
        "steamcmd", _server(id=3), progress_callback=progress, max_retries=0
    )
    assert cancelled[0] is False

    monkeypatch.setattr("services.ssh.game_steamcmd._legacy_cancel_requested", AsyncMock(return_value=False))
    manager._prepare_steamcmd_retry = AsyncMock(return_value=True)
    manager._run_steamcmd_attempt = AsyncMock(
        return_value=(False, "bad", "fatal", "fatal", False)
    )
    failed = await manager._execute_steamcmd_with_retry(
        "steamcmd", _server(id=3), progress_callback=progress, max_retries=0
    )
    assert failed == (False, "bad", "fatal")


@pytest.mark.asyncio
async def test_steamcmd_session_start_and_pid_helpers_cover_fallbacks(monkeypatch):
    manager = GameSteamcmdMixin()
    server = _server(id=3, game_directory="/srv/cs2", session_manager="tmux")
    manager.execute_command = AsyncMock(side_effect=[
        (True, "", ""),
        (True, "", ""),
        (True, "", ""),
        (True, "7\nnot-a-pid\n", ""),
    ])
    manager._steamcmd_session_running = AsyncMock(side_effect=[None, "tmux"])
    progress: list[str] = []

    async def send_progress(message: str):
        progress.append(message)

    started = await manager._start_steamcmd_session(
        "steamcmd", server, "tmux", "cs2steamcmd_3", "/tmp/exit", send_progress
    )
    assert started[0] is True
    assert any("Starting SteamCMD" in item for item in progress)

    manager._steamcmd_session_running = AsyncMock(return_value="screen")
    attached = await manager._start_steamcmd_session(
        "steamcmd", server, "tmux", "cs2steamcmd_3", "/tmp/exit", send_progress
    )
    assert attached[0] is True

    manager.execute_command = AsyncMock(return_value=(False, "", "offline"))
    assert await manager._list_steamcmd_pids(server) == []
    manager.execute_command = AsyncMock(return_value=(True, "12\nabc\n", ""))
    assert await manager._list_steamcmd_pids(server) == ["12"]
