"""补齐远程归档校验、解析和安全路径分支；所有 SSH 均为 fake。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest

from services.ssh_manager import SSHManager


def _manager():
    manager = SSHManager(use_pool=False)
    manager.conn = object()
    return manager


def test_archive_member_normalization_and_tar_decoding_edges():
    cls = SSHManager
    assert cls._normalize_archive_member("./addons/") == ("addons", None)
    assert cls._normalize_archive_member("addons\\server.cfg")[1]
    assert cls._normalize_archive_member("addons\\server.cfg", allow_backslash_separators=True) == (
        "addons/server.cfg",
        None,
    )
    for value in ("/etc/passwd", "C:/secret", "", "a//b", "../escape", "a\x00b", "a\\b"):
        path, error = cls._normalize_archive_member(value)
        assert path is None and error
    assert cls._normalize_archive_member(".") == (None, None)
    assert cls._normalize_archive_member(".\\", allow_backslash_separators=True) == (None, None)
    assert cls._decode_tar_listing_name(r"a\n\101\\") == ("a\nA\\", None)
    for value in ("a\\", r"a\9", r"a\777"):
        assert cls._decode_tar_listing_name(value)[1]
    assert cls._decode_tar_listing_name(r"\377")[1]
    good = '-rw-r--r-- 0/0 1 2026-01-01 00:00:00 "cfg/server.cfg"'
    assert cls._parse_tar_c_verbose_listing_line(good) == (("cfg/server.cfg", False), None)
    assert cls._parse_tar_c_verbose_listing_line(good.replace("-rw", "lrw"))[1]
    for line in ("short", "-rw-r--r-- 0/0 1 2026-01-01 00:00:00 cfg"):
        assert cls._parse_tar_c_verbose_listing_line(line)[1]


def test_archive_info_detects_duplicates_ancestors_and_folder_limits(monkeypatch):
    cls = SSHManager
    ok, info, error = cls._build_archive_info("zip", [("cfg", True), ("cfg/a.cfg", False)])
    assert ok and info["entry_count"] == 2 and not error
    for members in (
        [("cfg/a", False), ("cfg/a", False)],
        [("cfg", False), ("cfg/a", False)],
        [("a", False), ("a/b", False)],
    ):
        ok, _info, error = cls._build_archive_info("zip", members)
        assert not ok and error
    monkeypatch.setattr(cls, "ARCHIVE_MAX_ENTRIES", 1)
    assert not cls._build_archive_info("zip", [("a", False), ("b", False)])[0]
    monkeypatch.setattr(cls, "ARCHIVE_MAX_ENTRIES", 10000)
    monkeypatch.setattr(cls, "ARCHIVE_MAX_FOLDERS", 1)
    assert not cls._build_archive_info("zip", [("a/b/c", False)])[0]
    assert cls._tar_compress_program("tar.zst") == "zstd"
    assert cls._tar_compress_program("tar") is None
    assert "--transform" in cls._tar_extract_command("tar", "tar.gz", "a", "b", True)
    assert cls._single_file_output_name("a.zst", "zst") == "a"
    assert cls._single_file_output_name("a.dat", "gz") == "a.dat"
    assert "--format=lzma" in cls._single_file_command("xz", "lzma", "a", "t")


class _Sftp:
    def __init__(self, *, target=None, base=None, realpaths=None, lstat_error=None):
        self.target = target
        self.base = base or SimpleNamespace(type=2)
        self.realpaths = realpaths or {}
        self.lstat_error = lstat_error

    async def stat(self, path):
        if path == "/srv/cs2":
            return self.base
        raise asyncssh.SFTPError("missing base")

    async def lstat(self, path):
        if self.lstat_error:
            raise self.lstat_error
        if path == "/srv/cs2/file.zip" and self.target is not None:
            return self.target
        if path == "/srv/cs2":
            return self.base
        raise asyncssh.SFTPNoSuchFile("missing")

    async def realpath(self, path):
        return self.realpaths.get(path, path)


class _SftpContext:
    def __init__(self, sftp):
        self.sftp = sftp

    async def __aenter__(self):
        return self.sftp

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_archive_remote_path_validation_covers_missing_symlinks_and_io(monkeypatch):
    manager = _manager()
    server = SimpleNamespace(game_directory="/srv/cs2")
    regular = SimpleNamespace(type=1)
    manager.conn = SimpleNamespace(
        start_sftp_client=lambda: _SftpContext(
            _Sftp(
                target=regular,
                realpaths={"/srv/cs2": "/srv/cs2", "/srv/cs2/file.zip": "/srv/cs2/file.zip"},
            )
        )
    )
    assert await manager.validate_path_within_base(
        "/srv/cs2", "/srv/cs2/file.zip", server, require_regular=True
    ) == (
        True,
        "",
    )
    assert await manager.validate_path_within_base("/srv/cs2", "/etc/file.zip", server) == (
        False,
        "Path is outside the server directory",
    )
    assert await manager.validate_path_within_base(
        "/srv/cs2", "/srv/cs2/new/file.zip", server, allow_missing=True
    ) == (
        True,
        "",
    )
    assert await manager.validate_path_within_base(
        "/srv/cs2", "/srv/cs2/missing.zip", server, allow_missing=True, require_regular=True
    ) == (
        False,
        "Archive file does not exist",
    )

    outside = SimpleNamespace(
        start_sftp_client=lambda: _SftpContext(
            _Sftp(
                target=regular,
                realpaths={"/srv/cs2": "/srv/cs2", "/srv/cs2/file.zip": "/outside/file.zip"},
            )
        )
    )
    manager.conn = outside
    assert (
        "outside"
        in (await manager.validate_path_within_base("/srv/cs2", "/srv/cs2/file.zip", server))[1]
    )
    manager.conn = SimpleNamespace(
        start_sftp_client=lambda: _SftpContext(_Sftp(base=SimpleNamespace(type=1)))
    )
    assert (
        "not a directory"
        in (await manager.validate_path_within_base("/srv/cs2", "/srv/cs2/file.zip", server))[1]
    )

    manager.conn = None
    manager.connect = AsyncMock(return_value=(False, "offline"))
    assert await manager.validate_path_within_base("/srv/cs2", "/srv/cs2/a", server) == (
        False,
        "Connection failed: offline",
    )


@pytest.mark.asyncio
async def test_archive_inspection_dispatch_and_stream_failure_paths(monkeypatch):
    manager = _manager()
    server = SimpleNamespace(game_directory="/srv/cs2")
    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    manager._inspect_archive_connected = AsyncMock(return_value=(True, {"ok": 1}, ""))
    assert await manager.inspect_archive("/srv/cs2/a.zip", server) == (True, {"ok": 1}, "")
    assert "Unsupported" in (await manager.inspect_archive("/srv/cs2/a.unknown", server))[2]
    manager.validate_path_within_base.return_value = (False, "bad path")
    assert await manager.inspect_archive("/srv/cs2/a.zip", server) == (False, {}, "bad path")

    manager.conn = None
    assert await manager._stream_archive_listing("x", lambda _line: None) == (
        False,
        "Not connected",
    )

    class _Proc:
        class _Err:
            def __init__(self):
                self.chunks = [b"ssh failed", b""]

            async def read(self, _size):
                return self.chunks.pop(0)

        stderr = _Err()
        stdout = SimpleNamespace(read=AsyncMock(return_value=b""))
        wait = AsyncMock(return_value=SimpleNamespace(exit_status=2))

    manager.conn = SimpleNamespace(create_process=AsyncMock(return_value=_Proc()))
    ok, error = await manager._stream_archive_listing("x", lambda _line: None)
    assert not ok and error == "ssh failed"

    async def broken(*_args, **_kwargs):
        raise asyncssh.ConnectionLost("lost")

    manager.conn.create_process = broken
    ok, error = await manager._stream_archive_listing("x", lambda _line: None)
    assert not ok and "connection lost" in error.lower()
