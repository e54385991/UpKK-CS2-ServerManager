"""补充旧版插件目录、安装器和卸载器的隔离错误矩阵。"""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile

from api.routes import plugins
from modules import InstalledPlugin, Plugin, PluginCategory, PluginInstallRequest


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Db:
    def __init__(self, gets=None, execute_value=None):
        self.gets = list(gets or [])
        self.execute_value = execute_value
        self.added = []
        self.commits = 0
        self.deleted = []

    async def get(self, *_args):
        return self.gets.pop(0) if self.gets else None

    async def execute(self, _query):
        return _Result(self.execute_value)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None

    async def delete(self, value):
        self.deleted.append(value)


def _server():
    return SimpleNamespace(id=3, user_id=1, game_directory="/srv/cs2")


def _plugin(**overrides):
    values = dict(
        id=7,
        name="demo-plugin",
        display_name="Demo",
        description="demo",
        category=PluginCategory.UTILITY,
        version="1.2",
        download_url="https://example.invalid/demo.tar.gz",
        author=None,
        homepage=None,
        dependencies=None,
        install_path="addons/plugins",
        config_required=False,
        enabled=True,
    )
    values.update(overrides)
    return Plugin(**values)


@pytest.mark.asyncio
async def test_install_plugin_to_server_covers_each_remote_stage(monkeypatch):
    server = _server()
    plugin = _plugin()
    db = _Db()

    def ssh_factory(connect_result=(True, "ok"), responses=None):
        ssh = SimpleNamespace(
            connect=AsyncMock(return_value=connect_result),
            disconnect=AsyncMock(),
            execute_command=AsyncMock(side_effect=responses or [(True, "", "")] * 4),
        )
        monkeypatch.setattr(plugins, "SSHManager", lambda: ssh)
        return ssh

    ssh = ssh_factory()
    assert await plugins._install_plugin_to_server(server, plugin, None, "cfg", db) is True
    assert isinstance(db.added[-1], InstalledPlugin)
    ssh.disconnect.assert_awaited_once()

    for connect_result, responses in [
        ((False, "offline"), []),
        ((True, "ok"), [(False, "", "mkdir")]),
        ((True, "ok"), [(True, "", ""), (False, "", "wget")]),
        ((True, "ok"), [(True, "", ""), (True, "", ""), (False, "", "tar")]),
    ]:
        db = _Db()
        ssh = ssh_factory(connect_result=connect_result, responses=responses)
        assert await plugins._install_plugin_to_server(server, plugin, "https://x/a", None, db) is False
        ssh.disconnect.assert_awaited_once()

    db = _Db()
    ssh = ssh_factory()
    ssh.connect.side_effect = RuntimeError("connection exploded")
    assert await plugins._install_plugin_to_server(server, plugin, None, None, db) is False


@pytest.mark.asyncio
async def test_plugin_install_dependencies_invalid_and_existing_states(monkeypatch):
    user = SimpleNamespace(id=1)
    server = _server()
    plugin = _plugin(dependencies="not-json")
    monkeypatch.setattr(plugins.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    monkeypatch.setattr(plugins.InstalledPlugin, "get_by_server_and_plugin", AsyncMock(return_value=None))
    monkeypatch.setattr(plugins, "_install_plugin_to_server", AsyncMock(return_value=True))
    db = _Db([plugin])
    request = PluginInstallRequest(plugin_id=7)
    assert (await plugins.install_plugin(3, request, user, db, server)).status_code == 200

    monkeypatch.setattr(plugins.InstalledPlugin, "get_by_server_and_plugin", AsyncMock(return_value=object()))
    with pytest.raises(HTTPException, match="already installed"):
        await plugins.install_plugin(3, request, user, _Db([plugin]), server)
    monkeypatch.setattr(plugins.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    with pytest.raises(HTTPException, match="Server not found"):
        await plugins.install_plugin(3, request, user, _Db(), server)
    monkeypatch.setattr(plugins.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    with pytest.raises(HTTPException, match="Plugin not found"):
        await plugins.install_plugin(3, request, user, _Db([None]), server)

    monkeypatch.setattr(plugins.InstalledPlugin, "get_by_server_and_plugin", AsyncMock(return_value=None))
    monkeypatch.setattr(plugins, "_install_plugin_to_server", AsyncMock(return_value=False))
    with pytest.raises(HTTPException, match="Failed to install"):
        await plugins.install_plugin(3, request, user, _Db([plugin]), server)


@pytest.mark.asyncio
async def test_plugin_uninstall_and_installed_listing_cover_missing_and_ssh_states(monkeypatch):
    class _Query:
        def where(self, *_args):
            return self

    monkeypatch.setattr(plugins, "select", lambda *_args: _Query())
    class _Installed:
        id = 0
        server_id = 0

    monkeypatch.setattr(plugins, "InstalledPlugin", _Installed)
    user = SimpleNamespace(id=1)
    server = _server()
    installed = SimpleNamespace(id=4, server_id=3, plugin_id=7)
    plugin = _plugin()
    monkeypatch.setattr(plugins.Server, "get_by_id_and_user", AsyncMock(return_value=server))
    with pytest.raises(HTTPException, match="Installed plugin not found"):
        await plugins.uninstall_plugin(3, 4, user, _Db(), server)

    for connect_result in ((True, "ok"), (False, "offline")):
        db = _Db([plugin], installed)
        ssh = SimpleNamespace(
            connect=AsyncMock(return_value=connect_result),
            execute_command=AsyncMock(return_value=(True, "", "")),
            disconnect=AsyncMock(),
        )
        monkeypatch.setattr(plugins, "SSHManager", lambda: ssh)
        result = await plugins.uninstall_plugin(3, 4, user, db, server)
        assert result.status_code == 200
        assert db.deleted == [installed]
        ssh.disconnect.assert_awaited_once()

    monkeypatch.setattr(plugins.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    with pytest.raises(HTTPException, match="Server not found"):
        await plugins.uninstall_plugin(3, 4, user, _Db(), server)


@pytest.mark.asyncio
async def test_plugin_upload_size_and_database_failure_cleanup(monkeypatch, tmp_path):
    admin = SimpleNamespace(is_admin=True)
    monkeypatch.setattr(plugins.os, "getcwd", lambda: str(tmp_path))
    monkeypatch.setattr(plugins, "MAX_PLUGIN_UPLOAD_BYTES", 3)
    with pytest.raises(HTTPException) as exc_info:
        await plugins.upload_plugin(
            UploadFile(filename="demo.tar.gz", file=io.BytesIO(b"1234")),
            "demo", "Demo", "desc", "utility", "1", None, None, None,
            "addons/plugins", False, current_user=admin, db=_Db()
        )
    assert exc_info.value.status_code == 413
    assert not list((tmp_path / "static/uploads/plugins").glob("*"))

    class FailingDb(_Db):
        async def commit(self):
            self.commits += 1
            if self.commits > 1:
                raise RuntimeError("database unavailable")

    result_db = FailingDb()
    with pytest.raises(HTTPException, match="Failed to upload"):
        await plugins.upload_plugin(
            UploadFile(filename="demo.tar.gz", file=io.BytesIO(b"ok")),
            "demo", "Demo", "desc", "utility", "1", None, None, None,
            "addons/plugins", False, current_user=admin, db=result_db
        )
    assert not list((tmp_path / "static/uploads/plugins").glob("*"))
