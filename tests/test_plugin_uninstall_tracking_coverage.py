"""覆盖插件文件卸载和安装跟踪元数据的纯本地分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import plugin_uninstall as uninstall
from services.plugins import tracking


class _Result:
    def __init__(self, item=None):
        self.item = item

    def scalar_one_or_none(self):
        return self.item


class _Context:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return None


class _Db:
    def __init__(self, item=None):
        self.item = item
        self.added = []
        self.commits = 0

    async def execute(self, _query):
        return _Result(self.item)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _item):
        return None


def _server():
    return SimpleNamespace(game_directory="/srv/cs2")


def test_uninstall_path_normalization_and_tracking_helpers():
    assert uninstall.validate_uninstall_path(r" addons\\foo.dll ") == "addons/foo.dll"
    assert uninstall.normalize_uninstall_paths(["a", "a", "./b"]) == ["a", "b"]
    for value in ("", "/absolute", "../escape", "a/../../b", "a\x00b"):
        with pytest.raises(ValueError):
            uninstall.validate_uninstall_path(value)
    with pytest.raises(ValueError, match="at least one"):
        uninstall.normalize_uninstall_paths([])

    assert (
        tracking.canonical_repo_url(" https://github.com/Org/Repo.git/ ")
        == "https://github.com/Org/Repo"
    )
    assert (
        tracking.repo_api_url("https://github.com/Org/Repo")
        == "https://api.github.com/repos/Org/Repo/releases/latest"
    )
    with pytest.raises(ValueError):
        tracking.canonical_repo_url("https://gitlab.com/a/b")
    assert tracking.derive_asset_glob(None, "v1") is None
    assert tracking.derive_asset_glob("plugin-v1.zip", "v1") == "plugin-*.zip"
    assert tracking.derive_asset_glob("plugin.zip", "v1") == "plugin.zip"


@pytest.mark.asyncio
async def test_uninstall_plugin_files_success_partial_connection_and_exception(monkeypatch):
    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        disconnect=AsyncMock(),
        execute_command=AsyncMock(side_effect=[(True, "", ""), (False, "", "denied")]),
    )
    monkeypatch.setattr(uninstall, "SSHManager", lambda: ssh)
    progress = AsyncMock()
    result = await uninstall.uninstall_plugin_files(
        server=_server(), files_to_delete=["addons/a.dll", "addons/b.dll"], progress=progress
    )
    assert result == {
        "success": False,
        "message": "Uninstallation completed with errors. Deleted 1 files, failed 1 files.",
        "deleted_files": 1,
        "failed_files": ["addons/b.dll"],
    }
    ssh.disconnect.assert_awaited_once()
    assert progress.await_count >= 3

    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(False, "offline")), disconnect=AsyncMock()
    )
    monkeypatch.setattr(uninstall, "SSHManager", lambda: ssh)
    result = await uninstall.uninstall_plugin_files(server=_server(), files_to_delete=["a"])
    assert result["failed_files"] == ["a"]
    ssh.disconnect.assert_not_awaited()

    ssh = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "ok")),
        disconnect=AsyncMock(),
        execute_command=AsyncMock(side_effect=RuntimeError("remote")),
    )
    monkeypatch.setattr(uninstall, "SSHManager", lambda: ssh)
    result = await uninstall.uninstall_plugin_files(server=_server(), files_to_delete=["a"])
    assert result["message"] == "Uninstallation error: remote"
    ssh.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_managed_plugin_creates_and_refreshes_without_auto_update(monkeypatch):
    db = _Db()
    monkeypatch.setattr(tracking, "async_session_maker", lambda: _Context(db))
    item = await tracking.upsert_managed_plugin(
        server_id=3,
        source_type="github",
        source_key="Org/Repo",
        display_name="Repo",
        repo_url="https://github.com/Org/Repo.git",
        installed_version="",
        installed_asset_name="repo-v1.zip",
        exclude_dirs=["cfg"],
        exclude_files=["x"],
    )
    assert item.display_name == "Repo"
    assert item.repo_url == "https://github.com/Org/Repo"
    assert item.installed_version == "unknown"
    assert item.auto_update_enabled is False
    assert db.commits == 1

    existing = SimpleNamespace(
        repo_url="https://github.com/Old/Repo",
        installed_asset_name="old.zip",
        asset_glob="old-*",
    )
    db = _Db(existing)
    monkeypatch.setattr(tracking, "async_session_maker", lambda: _Context(db))
    result = await tracking.upsert_managed_plugin(
        server_id=3,
        source_type="market",
        source_key="4",
        display_name="Updated",
        installed_release_id="release",
        installed_version="2.0",
        custom_install_path="/srv/cs2",
    )
    assert result is existing
    assert existing.last_status == "installed"
    assert existing.exclude_dirs == []

    with pytest.raises(ValueError):
        await tracking.upsert_managed_plugin(
            server_id=3, source_type="github", source_key="x", display_name="x", repo_url="bad"
        )
