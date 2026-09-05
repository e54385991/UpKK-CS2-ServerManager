"""覆盖插件配置远程扫描、路径安全和原子写入的隔离分支。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asyncssh.constants import FILEXFER_TYPE_DIRECTORY, FILEXFER_TYPE_REGULAR, FILEXFER_TYPE_SYMLINK

from services.plugin_configs import remote
from services.ssh import file_archive


def _server(**overrides):
    values = {"game_directory": "/srv/cs2"}
    values.update(overrides)
    return SimpleNamespace(**values)


class _Sftp:
    def __init__(self, attrs=None, entries=(), data=b"text"):
        self.attrs = attrs or SimpleNamespace(
            type=FILEXFER_TYPE_DIRECTORY, size=0, mtime=1, permissions=0o644
        )
        self.entries = list(entries)
        self.data = data
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def lstat(self, _path):
        return self.attrs

    async def stat(self, _path):
        return self.attrs

    async def realpath(self, path):
        return path

    def exit(self):
        self.closed = True

    async def wait_closed(self):
        return None

    async def scandir(self, _path):
        for entry in self.entries:
            yield entry

    def open(self, _path, *_args, **_kwargs):
        return _File(self.data)

    async def chmod(self, *_args):
        return None


class _File:
    def __init__(self, data=b""):
        self.data = data
        self.written = b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self, *_args):
        return self.data

    async def write(self, data):
        self.written += data


class _AwaitableSftpConnection:
    def __init__(self, sftp):
        self.sftp = sftp

    async def start_sftp_client(self):
        return self.sftp


class _ContextSftpConnection:
    def __init__(self, sftp):
        self.sftp = sftp

    def start_sftp_client(self):
        return self.sftp


def _manager(sftp, *, valid=True, connected=True, context=False):
    manager = SimpleNamespace()
    manager.conn = _ContextSftpConnection(sftp) if context else _AwaitableSftpConnection(sftp)
    manager.validate_path_within_base = AsyncMock(
        return_value=(valid, "outside" if not valid else "")
    )
    return manager


def test_remote_scan_parsers_and_command_helpers(monkeypatch):
    assert remote.absolute_path(_server(), "cfg/a.cfg") == "/srv/cs2/cfg/a.cfg"
    assert remote._source_kind(FILEXFER_TYPE_DIRECTORY) == "directory"
    assert remote._source_kind(FILEXFER_TYPE_REGULAR) == "file"
    assert remote._source_kind(FILEXFER_TYPE_SYMLINK) == "unsupported"
    assert remote._decode_scan_token(b"ok.cfg") == "ok.cfg"
    with pytest.raises(remote.PluginConfigError, match="non-UTF"):
        remote._decode_scan_token(b"\xff")
    with pytest.raises(remote.PluginConfigError, match="unsafe"):
        remote._decode_scan_token(b"bad\nname")
    assert remote._append_scan_token(b"D", None, ())[0] == "D"
    assert remote._append_scan_token(b"x", "D", ("old",))[2] == ("D", ["old", "x"])
    assert remote._append_scan_token(b"x", "F", ("name",))[2] is None
    with pytest.raises(remote.PluginConfigError, match="invalid record"):
        remote._append_scan_token(b"X", None, [])
    event, count = remote._scan_record_event("D", ["cfg"], "root", 0)
    assert event["type"] == "progress" and count == 0
    event, count = remote._scan_record_event("F", ["cfg/a.cfg", "12", "1.5"], "root", 0)
    assert event["file"]["path"] == "root/cfg/a.cfg" and count == 1
    with pytest.raises(remote.PluginConfigError, match="metadata"):
        remote._scan_record_event("F", ["a.cfg", "bad", "1"], "root", 0)
    assert "find -P" in remote._scan_command("/srv/cs2/cfg")


@pytest.mark.asyncio
async def test_sftp_source_inspection_browse_and_path_failures():
    directory = _Sftp()
    manager = _manager(directory)
    assert await remote.inspect_source(manager, _server(), "cfg") == "directory"
    regular = _Sftp(SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=3, mtime=1, permissions=0o644))
    assert await remote.inspect_source(_manager(regular), _server(), "cfg/a.cfg") == "file"
    unsupported = _Sftp(SimpleNamespace(type=999, size=0, mtime=0, permissions=None))
    with pytest.raises(remote.PluginConfigError, match="regular file"):
        await remote.inspect_source(_manager(unsupported), _server(), "cfg/a.cfg")
    with pytest.raises(remote.PluginConfigError, match="outside"):
        await remote.inspect_source(_manager(directory, valid=False), _server(), "../etc")
    no_conn = SimpleNamespace(
        conn=None, validate_path_within_base=AsyncMock(return_value=(True, ""))
    )
    with pytest.raises(remote.PluginConfigError, match="not established"):
        await remote.inspect_source(no_conn, _server(), "cfg")

    entries = [
        SimpleNamespace(filename=".", attrs=SimpleNamespace(type=FILEXFER_TYPE_DIRECTORY, size=0)),
        SimpleNamespace(filename="link", attrs=SimpleNamespace(type=FILEXFER_TYPE_SYMLINK, size=0)),
        SimpleNamespace(
            filename="folder", attrs=SimpleNamespace(type=FILEXFER_TYPE_DIRECTORY, size=0)
        ),
        SimpleNamespace(
            filename="b.cfg", attrs=SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=5)
        ),
        SimpleNamespace(filename="socket", attrs=SimpleNamespace(type=999, size=0)),
    ]
    browsed = await remote.browse_directory(_manager(_Sftp(entries=entries)), _server(), "cfg")
    assert [item["name"] for item in browsed] == ["folder", "b.cfg", "link"]
    not_dir = _Sftp(SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=1, mtime=0, permissions=None))
    with pytest.raises(remote.PluginConfigError, match="not a directory"):
        await remote.browse_directory(_manager(not_dir), _server(), "cfg")


class _Stream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def read(self, _size):
        return next(self.chunks, b"")


class _Process:
    def __init__(self, stdout_chunks, stderr_chunks=(), exit_status=0):
        self.stdout = _Stream(stdout_chunks)
        self.stderr = _Stream(stderr_chunks)
        self.exit_status = exit_status
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait(self):
        return None


class _ScanConnection:
    def __init__(self, process):
        self.process = process
        self.created = []

    async def start_sftp_client(self):
        return _Sftp()

    async def create_process(self, *_args, **_kwargs):
        self.created.append(True)
        return self.process


@pytest.mark.asyncio
async def test_streaming_directory_scan_file_source_and_limits(monkeypatch):
    process = _Process([b"D\0cfg\0F\0demo.cfg\0" + b"12\0" + b"1.5\0"], [b"warning\n"])
    sftp = _Sftp()
    manager = _manager(sftp)
    manager.conn = _ScanConnection(process)
    events = [
        event async for event in remote.iter_source_scan(manager, _server(), "cfg", "directory")
    ]
    assert (
        events[0]["type"] == "progress" and events[1]["type"] == "file" and events[-1]["count"] == 1
    )
    manager.conn = _ScanConnection(_Process([b"D\0cfg\0F\0demo.cfg\0" + b"12\0" + b"1.5\0"]))
    result = await remote.scan_source(manager, _server(), "cfg", "directory")
    assert result["count"] == 1 and len(result["files"]) == 1

    file_sftp = _Sftp(
        SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=12, mtime=2, permissions=0o644)
    )
    file_manager = _manager(file_sftp)
    file_events = [
        event
        async for event in remote.iter_source_scan(file_manager, _server(), "cfg/demo.cfg", "file")
    ]
    assert file_events[0]["file"]["name"] == "demo.cfg"
    with pytest.raises(remote.PluginConfigError, match="type changed"):
        [
            event
            async for event in remote.iter_source_scan(_manager(_Sftp()), _server(), "cfg", "file")
        ]

    bad_process = _Process([b"F\0bad.cfg\0bad\0" + b"1\0"], [b"stderr"], exit_status=1)
    bad_manager = _manager(_Sftp())
    bad_manager.conn = _ScanConnection(bad_process)
    with pytest.raises(remote.PluginConfigError, match="invalid file metadata"):
        [
            event
            async for event in remote.iter_source_scan(bad_manager, _server(), "cfg", "directory")
        ]

    timeout_process = _Process([])

    async def timeout_read(_size):
        raise asyncio.TimeoutError

    timeout_process.stdout.read = timeout_read
    timeout_manager = _manager(_Sftp())
    timeout_manager.conn = _ScanConnection(timeout_process)
    with pytest.raises(remote.PluginConfigError, match="timed out"):
        [
            event
            async for event in remote.iter_source_scan(
                timeout_manager, _server(), "cfg", "directory"
            )
        ]

    monkeypatch.setattr(remote, "MAX_SOURCE_FILES", 0)
    truncated = _Process([b"F\0a.cfg\0" + b"1\0" + b"1\0"])
    truncated_manager = _manager(_Sftp())
    truncated_manager.conn = _ScanConnection(truncated)
    events = [
        event
        async for event in remote.iter_source_scan(truncated_manager, _server(), "cfg", "directory")
    ]
    assert events[-1]["truncated"]


@pytest.mark.asyncio
async def test_read_write_and_process_cleanup_paths(monkeypatch):
    server = _server()
    manager = _manager(
        _Sftp(
            SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=4, mtime=0, permissions=0o600),
            data=b"hello",
        ),
        context=True,
    )
    assert await remote.read_text_file(manager, server, "cfg/a.cfg") == "hello"
    too_large = _Sftp(
        SimpleNamespace(
            type=FILEXFER_TYPE_REGULAR, size=remote.MAX_CONFIG_BYTES + 1, mtime=0, permissions=0o600
        )
    )
    with pytest.raises(remote.PluginConfigError, match="10 MiB"):
        await remote.read_text_file(_manager(too_large, context=True), server, "cfg/a.cfg")
    binary = _Sftp(
        SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=1, mtime=0, permissions=0o600),
        data=b"a\0b",
    )
    with pytest.raises(remote.PluginConfigError, match="Binary"):
        await remote.read_text_file(_manager(binary, context=True), server, "cfg/a.cfg")
    invalid_utf8 = _Sftp(
        SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=1, mtime=0, permissions=0o600),
        data=b"\xff",
    )
    with pytest.raises(remote.PluginConfigError, match="UTF-8"):
        await remote.read_text_file(_manager(invalid_utf8, context=True), server, "cfg/a.cfg")
    manager.validate_path_within_base.return_value = (False, "bad path")
    with pytest.raises(remote.PluginConfigError, match="bad path"):
        await remote.read_text_file(manager, server, "cfg/a.cfg")

    writable = _Sftp(
        SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=4, mtime=0, permissions=0o640)
    )
    write_manager = _manager(writable, context=True)
    write_manager.execute_command = AsyncMock(return_value=(True, "", ""))
    await remote.atomic_write_text_file(write_manager, server, "cfg/a.cfg", "updated")
    assert write_manager.execute_command.await_count == 1
    write_manager.execute_command = AsyncMock(return_value=(False, "", "replace failed"))
    with pytest.raises(remote.PluginConfigError, match="replace failed"):
        await remote.atomic_write_text_file(write_manager, server, "cfg/a.cfg", "updated")
    no_conn = SimpleNamespace(
        conn=None, validate_path_within_base=AsyncMock(return_value=(True, ""))
    )
    with pytest.raises(remote.PluginConfigError, match="not established"):
        await remote.atomic_write_text_file(no_conn, server, "cfg/a.cfg", "x")


@pytest.mark.asyncio
async def test_archive_stream_and_scan_stop_error_paths(monkeypatch):
    manager = file_archive.ArchiveOperationsMixin()
    manager.ARCHIVE_LISTING_READ_BYTES = 64
    manager.ARCHIVE_LISTING_ERROR_BYTES = 64
    manager.ARCHIVE_MAX_ENTRIES = 10
    manager.ARCHIVE_LISTING_MAX_LINE_BYTES = 8
    manager.ARCHIVE_LISTING_STOP_TIMEOUT = 0.1
    assert await manager._read_archive_stderr(_Stream(["one", b"two", b""])) == "onetwo"
    seen = []
    assert (
        await manager._consume_archive_stdout(
            _Stream([b"one\n", b"two"]), lambda line: seen.append(line)
        )
        is None
    )
    assert seen == ["one", "two"]
    manager.ARCHIVE_MAX_ENTRIES = 1
    monkeypatch.setattr(manager, "ARCHIVE_MAX_ENTRIES", 1)
    assert await manager._consume_archive_stdout(_Stream([b"one\ntwo\n"]), lambda _line: None)
    assert await manager._consume_archive_stdout(
        _Stream([b"a" * (manager.ARCHIVE_LISTING_MAX_LINE_BYTES + 1)]), lambda _line: None
    )
    assert await manager._consume_archive_stdout(_Stream([b"\xff\n"]), lambda _line: None)
    manager.conn = None
    assert await manager._stream_archive_listing("cmd", lambda _line: None) == (
        False,
        "Not connected",
    )

    process = _Process([])
    process.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), None])
    await file_archive.ArchiveOperationsMixin._stop_archive_listing_process(process)
    assert process.killed
    process.wait = AsyncMock(side_effect=RuntimeError("closed"))
    assert await file_archive.ArchiveOperationsMixin._stop_archive_listing_process(process)
