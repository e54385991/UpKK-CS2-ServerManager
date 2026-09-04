"""覆盖 GitHub 插件安装器的代理下载、归档校验和回滚分支。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules import GitHubPluginInstallRequest, GitHubPluginInstallResponse
from services import plugin_installation as installation


def _server(**overrides):
    values = {
        "id": 3,
        "user_id": 7,
        "game_directory": "/srv/cs2",
        "use_panel_proxy": False,
        "github_proxy": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(**overrides):
    values = {
        "download_url": "https://github.com/acme/demo/releases/download/v1/demo.zip",
        "suppress_notification": True,
        "record_installation": False,
    }
    values.update(overrides)
    return GitHubPluginInstallRequest(**values)


class _ScriptedSSH:
    mode = "ok"
    digest = "a" * 64
    count_values = ["2", "5"]

    def __init__(self):
        self.commands = []
        self.count_index = 0
        type(self).last = self

    async def connect(self, _server):
        return True, "connected"

    async def disconnect(self):
        return None

    async def upload_file_with_progress(self, _local, _remote, _server, **kwargs):
        callback = kwargs.get("progress_callback")
        if callback is not None:
            await callback(0, 0)
            await callback(100, 100)
        return (False, "upload failed") if self.mode == "upload-error" else (True, "")

    async def execute_command(self, command, **_kwargs):
        self.commands.append(command)
        if command.startswith("test -d /srv/cs2/cs2/game/csgo"):
            return (False, "", "") if self.mode == "missing-cs2" else (True, "exists", "")
        if "echo 'addons_found'" in command:
            return (True, "", "") if self.mode in {"no-addons", "subdir", "custom", "custom-error", "allowed-missing", "allowed-copy-error"} else (True, "addons_found", "")
        if command.startswith("curl "):
            return (False, "", "download failed") if self.mode == "download-error" else (True, "", "")
        if command.startswith("stat "):
            if self.mode == "invalid-size":
                return False, "", "stat failed"
            return True, "10" if self.mode == "small-size" else "2048", ""
        if "sha256sum --" in command:
            return True, self.digest, ""
        if command.startswith("command -v 7z"):
            return True, "", ""
        if "unzip -o" in command or command.startswith("tar -x") or "7za x" in command:
            return (False, "", "bad archive") if self.mode == "extract-error" else (True, "", "")
        if command.startswith("test -d /tmp/"):
            if self.mode == "prefix-error":
                return False, "", ""
            if self.mode == "allowed-missing" and "/addons" in command:
                return False, "", ""
            return True, "", ""
        if "-name 'addons'" in command:
            if self.mode in {"subdir"}:
                return True, "/tmp/upkk-plugin-3-run/extracted/package/addons\n", ""
            return True, "", ""
        if "test -d" in command and "addons" in command:
            return (False, "", "") if self.mode == "allowed-missing" else (True, "", "")
        if command.startswith("cp -a --no-dereference"):
            return (False, "", "stage failed") if self.mode == "allowed-copy-error" else (True, "", "")
        if command.startswith("command -v rsync"):
            return (True, "", "") if self.mode in {"no-rsync", "custom", "custom-error"} else (True, "/usr/bin/rsync", "")
        if "-type f" in command and "wc -l" in command:
            value = self.count_values[min(self.count_index, len(self.count_values) - 1)]
            self.count_index += 1
            return True, value, ""
        if command.startswith("sh -c") and "source-files.txt" in command:
            return (False, "", "backup failed") if self.mode == "backup-error" else (True, "", "")
        if command.startswith("sh -c") and "manifest.tsv" in command:
            return (False, "", "rollback failed") if self.mode in {"rollback-error", "copy-rollback-error"} else (True, "", "")
        if "rsync -av" in command or "tar --exclude" in command or command.startswith("cp -r"):
            return (False, "", "copy failed") if self.mode in {"copy-error", "custom-error", "copy-rollback-error"} else (True, "", "")
        return True, "", ""


@pytest.mark.asyncio
async def test_panel_proxy_download_success_and_failures(monkeypatch, tmp_path: Path):
    server = _server(use_panel_proxy=True)
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=7)
    monkeypatch.setattr(installation, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(installation, "send_deployment_update", AsyncMock())
    monkeypatch.setattr(installation, "discord_notification_service", SimpleNamespace(queue_notify=Mock()))
    _ScriptedSSH.mode = "ok"

    async def download(_url, path, **kwargs):
        callback = kwargs["progress_callback"]
        await callback(0, 0)
        await callback(1024, 2048)
        await callback(2048, 2048)
        Path(path).write_bytes(b"x" * 2048)
        return True, ""

    monkeypatch.setattr("modules.http_helper.http_helper.download_file", download)
    monkeypatch.setattr(installation, "SSHManager", lambda: _ScriptedSSH())
    result = await installation.install_github_plugin(
        3, _request(suppress_notification=False), db, user, operation_id="proxy-op"
    )
    assert result.success and result.installed_files == 3

    async def failed_download(_url, _path, **_kwargs):
        return False, "offline"

    monkeypatch.setattr("modules.http_helper.http_helper.download_file", failed_download)
    result = await installation.install_github_plugin(3, _request(), db, user)
    assert not result.success and "download" in result.message

    async def missing_download(_url, _path, **_kwargs):
        return True, ""

    monkeypatch.setattr("modules.http_helper.http_helper.download_file", missing_download)
    result = await installation.install_github_plugin(3, _request(), db, user)
    assert not result.success and "not found" in result.message

    async def small_download(_url, path, **_kwargs):
        Path(path).write_bytes(b"x")
        return True, ""

    monkeypatch.setattr("modules.http_helper.http_helper.download_file", small_download)
    result = await installation.install_github_plugin(3, _request(), db, user)
    assert not result.success and "too small" in result.message

    async def valid_download(_url, path, **_kwargs):
        Path(path).write_bytes(b"x" * 2048)
        return True, ""

    monkeypatch.setattr("modules.http_helper.http_helper.download_file", valid_download)
    monkeypatch.setattr(installation, "SSHManager", lambda: _ScriptedSSH())
    _ScriptedSSH.mode = "upload-error"
    result = await installation.install_github_plugin(3, _request(), db, user)
    assert not result.success and "upload" in result.message
    _ScriptedSSH.mode = "ok"


@pytest.mark.asyncio
async def test_direct_download_archive_and_structure_failures(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=7)
    server = _server()
    monkeypatch.setattr(installation, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(installation, "send_deployment_update", AsyncMock())

    async def run(mode, **kwargs):
        _ScriptedSSH.mode = mode
        monkeypatch.setattr(installation, "SSHManager", lambda: _ScriptedSSH())
        return await installation.install_github_plugin(3, _request(**kwargs), db, user)

    assert "download failed" in (await run("download-error")).message
    assert "invalid" in (await run("invalid-size")).message
    assert "too small" in (await run("small-size")).message
    assert "extract" in (await run("extract-error")).message
    assert "source prefix" in (await run("prefix-error", source_prefix="payload")).message
    assert "No addons" in (await run("no-addons")).message
    assert "Invalid custom" in (await run("custom", custom_install_path="../escape")).message
    assert "Approved archive mapping" in (await run("allowed-missing", allowed_roots=["addons"])).message
    assert "stage approved" in (await run("allowed-copy-error", allowed_roots=["addons"])).message


@pytest.mark.asyncio
async def test_custom_subdirectory_copy_rollback_and_notifications(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=7)
    server = _server()
    monkeypatch.setattr(installation, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(installation, "send_deployment_update", AsyncMock())

    async def run(mode, **kwargs):
        _ScriptedSSH.mode = mode
        monkeypatch.setattr(installation, "SSHManager", lambda: _ScriptedSSH())
        return await installation.install_github_plugin(3, _request(**kwargs), db, user)

    custom = await run("custom", custom_install_path="addons/custom", exclude_files=["cfg/x"], exclude_dirs=["cfg"])
    assert custom.success and custom.installed_files == 2
    subdir = await run("subdir")
    assert subdir.success
    failed = await run("custom-error", custom_install_path="addons/custom", installation_plan_hash="b" * 64)
    assert not failed.success and "copy failed" in failed.message and "restored" in failed.message
    failed = await run("copy-rollback-error", installation_plan_hash="c" * 64)
    assert not failed.success and "Rollback failed" in failed.message


@pytest.mark.asyncio
async def test_secure_remote_digest_and_retry_exception_paths(monkeypatch, tmp_path: Path):
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=7)
    server = _server()
    archive = tmp_path / "demo.zip"
    archive.write_bytes(b"x")
    digest = "a" * 64
    monkeypatch.setattr(installation, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(installation, "send_deployment_update", AsyncMock())
    monkeypatch.setattr(
        "services.plugins.github_assets.download_release_asset",
        AsyncMock(return_value=(str(archive), digest, 1)),
    )
    _ScriptedSSH.mode = "ok"
    _ScriptedSSH.digest = "b" * 64
    monkeypatch.setattr(installation, "SSHManager", lambda: _ScriptedSSH())
    result = await installation.install_github_plugin(3, _request(expected_archive_sha256=digest), db, user)
    assert not result.success and "digest changed" in result.message
    _ScriptedSSH.digest = "a" * 64

    error_progress = AsyncMock(side_effect=RuntimeError("progress"))
    monkeypatch.setattr(
        installation,
        "install_github_plugin",
        AsyncMock(side_effect=PermissionError("denied")),
    )
    with pytest.raises(PermissionError):
        await installation.install_github_plugin_with_retry(3, _request(), db, user, ai_progress=error_progress)
    monkeypatch.setattr(
        installation,
        "install_github_plugin",
        AsyncMock(side_effect=RuntimeError("temporary")),
    )
    result = await installation.install_github_plugin_with_retry(3, _request(), db, user, max_retries=0, ai_progress=error_progress)
    assert not result.success and "temporary" in result.message
