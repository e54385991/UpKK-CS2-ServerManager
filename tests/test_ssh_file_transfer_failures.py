"""覆盖 SSH 文件传输的连接、父目录、SFTP 和进度异常。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest

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
    def __init__(self):
        self.writes = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self, _size):
        return b"payload" if not self.writes else b""

    async def write(self, value):
        self.writes.append(value)


class _Sftp:
    def __init__(self, *, error=None, attrs=None):
        self.error = error
        self.attrs = attrs or SimpleNamespace(type=0, size=7)
        self.file = _File()

    async def makedirs(self, *_args, **_kwargs):
        if self.error:
            raise self.error

    async def put(self, *_args):
        if self.error:
            raise self.error

    async def get(self, *_args):
        if self.error:
            raise self.error

    async def stat(self, *_args):
        if self.error:
            raise self.error
        return self.attrs

    async def open(self, *_args, **_kwargs):
        if self.error:
            return _Context(self.error)
        return _Context(self.file)


def _server():
    return SimpleNamespace(id=1, game_directory="/srv/cs2")


def _manager(sftp):
    manager = SSHManager(use_pool=False)
    manager.conn = SimpleNamespace(start_sftp_client=lambda: _Context(sftp))
    return manager


@pytest.mark.asyncio
async def test_transfer_connection_and_parent_error_paths(tmp_path):
    server = _server()
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    offline = SSHManager(use_pool=False)
    offline.connect = AsyncMock(return_value=(False, "offline"))
    assert await offline.upload_file("x", "/srv/a", server) == (False, "Connection failed: offline")
    assert await offline.download_file("/srv/a", str(tmp_path / "a"), server) == (False, "Connection failed: offline")
    assert await offline.get_file_size("/srv/a", server) == (False, None, "Connection failed: offline")
    with pytest.raises(RuntimeError, match="offline"):
        [chunk async for chunk in offline.stream_file("/srv/a", server)]
    assert await offline.upload_file_with_progress("x", "/srv/a", server) == (False, "Connection failed: offline")

    manager = _manager(_Sftp(error=OSError("mkdir failed")))
    manager.execute_command = AsyncMock(return_value=(False, "", "mkdir denied"))
    assert "mkdir denied" in (await manager.upload_file("/tmp/a", "/srv/nested/a", server))[1]
    manager.execute_command = AsyncMock(return_value=(False, "", "mkdir denied"))
    assert "mkdir denied" in (await manager.upload_file_with_progress(str(source), "/srv/nested/a", server))[1]


@pytest.mark.asyncio
async def test_transfer_sftp_and_generic_errors(tmp_path):
    server = _server()
    for error, label in ((asyncssh.SFTPError(1, "sftp"), "SFTP error"), (RuntimeError("generic"), "Error")):
        manager = _manager(_Sftp(error=error))
        manager.execute_command = AsyncMock(return_value=(True, "", ""))
        assert label in (await manager.upload_file("/tmp/a", "/srv/a", server))[1]
        assert label in (await manager.download_file("/srv/a", str(tmp_path / "a"), server))[1]
        assert label in (await manager.get_file_size("/srv/a", server))[2]

    manager = _manager(_Sftp(attrs=SimpleNamespace(type=0, size=7)))
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    progress = []
    structured = []

    async def structured_progress(payload):
        structured.append(payload)

    def progress_with_events(done, total):
        progress.append((done, total))

    progress_with_events.progress_event_callback = structured_progress
    assert await manager.upload_file_with_progress(
        str(source), "/srv/a", server, progress_with_events
    ) == (True, "")
    assert progress[-1] == (7, 7)
    assert structured[0]["phase"] == "upload"
    assert structured[-1]["bytes_transferred"] == 7
