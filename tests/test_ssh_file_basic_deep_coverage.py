"""覆盖 SSH 基础文件能力的连接、SFTP 和路径边界。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest
from asyncssh.constants import FILEXFER_TYPE_DIRECTORY, FILEXFER_TYPE_REGULAR

from services.ssh_manager import SSHManager


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value

    async def __aexit__(self, *_args):
        return None


class _File:
    def __init__(self, content=b"text"):
        self.content = content
        self.writes = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self):
        return self.content

    async def write(self, value):
        self.writes.append(value)


class _Sftp:
    def __init__(self, attrs=None, content=b"text"):
        self.attrs = attrs or SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=4, mtime=1, permissions=0o644)
        self.file = _File(content)
        self.stat_error = None
        self.calls = []

    async def scandir(self, _path):
        yield SimpleNamespace(
            filename="file.txt",
            attrs=SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=2, mtime=0, permissions=0o600),
        )
        yield SimpleNamespace(
            filename="addons",
            attrs=SimpleNamespace(type=FILEXFER_TYPE_DIRECTORY, size=None, mtime=None, permissions=None),
        )

    async def stat(self, _path):
        if self.stat_error:
            raise self.stat_error
        return self.attrs

    async def lstat(self, _path):
        if self.stat_error:
            raise self.stat_error
        return self.attrs

    def open(self, *_args, **_kwargs):
        self.calls.append(("open", _args))
        return _Context(self.file)

    async def makedirs(self, path):
        self.calls.append(("makedirs", path))

    async def rmtree(self, path):
        self.calls.append(("rmtree", path))

    async def remove(self, path):
        self.calls.append(("remove", path))

    async def rename(self, old, new):
        self.calls.append(("rename", old, new))


class _Conn:
    def __init__(self, sftp):
        self.sftp = sftp

    def start_sftp_client(self):
        return _Context(self.sftp)


def _server(**overrides):
    value = SimpleNamespace(game_directory="/srv/cs2", id=1)
    for key, item in overrides.items():
        setattr(value, key, item)
    return value


def _manager(sftp):
    manager = SSHManager(use_pool=False)
    manager.conn = _Conn(sftp)
    return manager


@pytest.mark.asyncio
async def test_list_and_read_cover_connection_sftp_retry_encoding_and_size(monkeypatch):
    server = _server()
    offline = SSHManager(use_pool=False)
    offline.connect = AsyncMock(return_value=(False, "offline"))
    assert await offline.list_directory("/srv/cs2", server) == (False, [], "Connection failed: offline")
    assert await offline.read_file("/srv/cs2/a", server) == (False, "", "Connection failed: offline")

    sftp = _Sftp()
    manager = _manager(sftp)
    ok, files, error = await manager.list_directory("/srv/cs2", server)
    assert ok and not error and files[0]["type"] == "directory"

    sftp = _Sftp(content="plain")
    manager = _manager(sftp)
    assert await manager.read_file("/srv/cs2/a", server) == (True, "plain", "")
    sftp.file = _File(b"\xff")
    assert (await manager.read_file("/srv/cs2/a", server))[1] == "ÿ"
    sftp.attrs.size = 100
    assert (await manager.read_file("/srv/cs2/a", server, max_size=10))[0] is False

    retry = _manager(_Sftp())
    retry.conn.start_sftp_client = lambda: _Context(asyncssh.SFTPError(1, "first"))
    retry._handle_sftp_error_with_reconnect = AsyncMock(return_value=[{"name": "retried"}])
    assert (await retry.list_directory("/srv/cs2", server))[0] is True
    retry._handle_sftp_error_with_reconnect = AsyncMock(side_effect=RuntimeError("exhausted"))
    assert (await retry.list_directory("/srv/cs2", server))[0] is False

    generic = _manager(_Sftp())
    generic.conn.start_sftp_client = lambda: _Context(RuntimeError("broken"))
    assert "Error listing" in (await generic.list_directory("/srv/cs2", server))[2]
    generic._handle_sftp_error_with_reconnect = AsyncMock(side_effect=RuntimeError("retry failed"))
    generic.conn.start_sftp_client = lambda: _Context(asyncssh.SFTPError(1, "read"))
    assert (await generic.read_file("/srv/cs2/a", server))[0] is False


@pytest.mark.asyncio
async def test_write_delete_create_and_rename_cover_all_remote_outcomes():
    server = _server()
    offline = SSHManager(use_pool=False)
    offline.connect = AsyncMock(return_value=(False, "offline"))
    assert await offline.write_file("/srv/cs2/a", "x", server) == (False, "Connection failed: offline")
    assert await offline.delete_path("/srv/cs2/a", server) == (False, "Connection failed: offline")
    assert await offline.create_directory("/srv/cs2/a", server) == (False, "Connection failed: offline")
    assert await offline.rename_path("/srv/cs2/a", "/srv/cs2/b", server) == (False, "Connection failed: offline")

    sftp = _Sftp()
    manager = _manager(sftp)
    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    assert await manager.write_file("/srv/cs2/nested/a", "hello", server) == (True, "")
    sftp.stat_error = asyncssh.SFTPNoSuchFile("missing")
    assert await manager.write_file("/srv/cs2/nested/a", "hello", server) == (True, "")
    assert any(call[0] == "makedirs" for call in sftp.calls)

    sftp.stat_error = None
    manager.conn.start_sftp_client = lambda: _Context(asyncssh.SFTPError(1, "write"))
    manager._handle_sftp_error_with_reconnect = AsyncMock(return_value=None)
    assert await manager.write_file("/srv/cs2/a", "x", server) == (True, "")
    manager._handle_sftp_error_with_reconnect = AsyncMock(side_effect=RuntimeError("retry failed"))
    assert (await manager.write_file("/srv/cs2/a", "x", server))[0] is False

    sftp = _Sftp(attrs=SimpleNamespace(type=FILEXFER_TYPE_DIRECTORY))
    manager = _manager(sftp)
    assert await manager.delete_path("/srv/cs2/addons", server) == (True, "")
    sftp.attrs = SimpleNamespace(type=FILEXFER_TYPE_REGULAR)
    assert await manager.delete_path("/srv/cs2/file", server) == (True, "")
    manager.conn.start_sftp_client = lambda: _Context(asyncssh.SFTPError(1, "delete"))
    assert "SFTP error" in (await manager.delete_path("/srv/cs2/file", server))[1]
    manager.conn.start_sftp_client = lambda: _Context(RuntimeError("delete"))
    assert "Error deleting" in (await manager.delete_path("/srv/cs2/file", server))[1]

    manager = _manager(_Sftp())
    manager.validate_path_within_base = AsyncMock(return_value=(False, "outside"))
    assert await manager.create_directory("/tmp/outside", server) == (False, "outside")
    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    assert await manager.create_directory("/srv/cs2/new", server) == (True, "")
    manager.conn.start_sftp_client = lambda: _Context(asyncssh.SFTPError(1, "mkdir"))
    assert "SFTP error" in (await manager.create_directory("/srv/cs2/new", server))[1]
    manager.conn.start_sftp_client = lambda: _Context(RuntimeError("mkdir"))
    assert "Error creating" in (await manager.create_directory("/srv/cs2/new", server))[1]

    manager = _manager(_Sftp())
    assert await manager.rename_path("/srv/cs2/a", "/srv/cs2/b", server) == (True, "")
    manager.conn.start_sftp_client = lambda: _Context(asyncssh.SFTPError(1, "rename"))
    assert "SFTP error" in (await manager.rename_path("/srv/cs2/a", "/srv/cs2/b", server))[1]
    manager.conn.start_sftp_client = lambda: _Context(RuntimeError("rename"))
    assert "Error renaming" in (await manager.rename_path("/srv/cs2/a", "/srv/cs2/b", server))[1]


@pytest.mark.asyncio
async def test_remote_exists_and_copy_path_validation_failures():
    server = _server()
    manager = SSHManager(use_pool=False)
    assert await manager._remote_exists("/srv/cs2/a") is False
    sftp = _Sftp()
    manager = _manager(sftp)
    assert await manager._remote_exists("/srv/cs2/a") is True
    sftp.stat_error = asyncssh.SFTPNoSuchFile("missing")
    assert await manager._remote_exists("/srv/cs2/a") is False
    sftp.stat_error = asyncssh.SFTPError(1, "bad")
    assert await manager._remote_exists("/srv/cs2/a") is False

    manager = _manager(_Sftp())
    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    manager._remote_exists = AsyncMock(return_value=False)
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    assert await manager.copy_into_directory("/srv/cs2/a", "/srv/cs2/d", server) == (
        True,
        "/srv/cs2/d/a",
        "",
    )
    assert await manager.copy_into_directory("/", "/srv/cs2/d", server) == (False, "", "Invalid source path")
    assert await manager.copy_into_directory("/srv/cs2/d", "/srv/cs2/d", server) == (
        False,
        "",
        "Cannot copy a folder into itself",
    )
    manager.validate_path_within_base = AsyncMock(side_effect=[(True, ""), (False, "bad destination")])
    assert (await manager.copy_into_directory("/srv/cs2/a", "/srv/cs2/d", server))[2] == "bad destination"
    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    manager.execute_command = AsyncMock(return_value=(False, "", "copy error"))
    assert "copy error" in (await manager.copy_into_directory("/srv/cs2/a", "/srv/cs2/d", server))[2]
