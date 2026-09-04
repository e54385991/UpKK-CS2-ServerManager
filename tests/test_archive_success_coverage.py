"""Cover the successful remote download and archive merge workflows."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest
from asyncssh.constants import FILEXFER_TYPE_DIRECTORY, FILEXFER_TYPE_REGULAR
from urllib.parse import urlsplit

from services.ssh.connection_download import DownloadConnectionMixin
from services.ssh.file_archive import ArchiveOperationsMixin
from services.ssh.file_download_extract import DownloadExtractMixin


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class _ArchiveSftp:
    def __init__(self):
        self.header = b"HTTP/1.1 200 OK\r\nContent-Type: application/zip\r\n\r\n"
        self.calls: list[tuple] = []

    async def lstat(self, path):
        self.calls.append(("lstat", path))
        if path.endswith(".headers"):
            return SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=len(self.header))
        if ".upkk-download-" in path and path.endswith(".part"):
            return SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=12)
        if path.endswith(".upkk-file-tasks") or "extract-" in path:
            return SimpleNamespace(type=FILEXFER_TYPE_DIRECTORY, size=0)
        raise asyncssh.SFTPNoSuchFile(path)

    async def realpath(self, path):
        return path

    def open(self, path, *_args, **_kwargs):
        self.calls.append(("open", path))
        return _Context(_HeaderFile(self.header))


class _HeaderFile:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self):
        return self.data


class _Connection:
    def __init__(self, sftp):
        self.sftp = sftp

    def start_sftp_client(self):
        return _Context(self.sftp)


def _server():
    return SimpleNamespace(id=3, game_directory="/srv/cs2")


class _Download(DownloadExtractMixin, DownloadConnectionMixin, ArchiveOperationsMixin):
    ARCHIVE_EXTRACT_TIMEOUT = 3600


def _manager(sftp):
    manager = _Download()
    manager.conn = _Connection(sftp)
    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    async def find_tool(candidates):
        return f"/usr/bin/{candidates[0]}"

    manager._find_remote_tool = AsyncMock(side_effect=find_tool)
    manager._validate_remote_download_url = lambda url: (urlsplit(url), "")
    manager._resolve_public_download_address = AsyncMock(return_value=("93.184.216.34", ""))
    return manager


@pytest.mark.asyncio
async def test_download_url_success_publishes_and_reports_resolved_target():
    sftp = _ArchiveSftp()
    manager = _manager(sftp)
    commands: list[str] = []

    async def execute(command, **_kwargs):
        commands.append(command)
        return True, "", ""

    manager.execute_command = execute
    callback = AsyncMock()
    success, error = await manager.download_url_to_file(
        "https://example.com/plugin.zip",
        "/srv/cs2/plugin.zip",
        _server(),
        overwrite=True,
        resolved_target_callback=callback,
    )

    assert (success, error) == (True, "")
    callback.assert_awaited_once_with("/srv/cs2/plugin.zip")
    assert any("mv -f" in command for command in commands)
    assert commands[-1].startswith("rm -f --")
    assert any("--resolve" in command and "93.184.216.34" in command for command in commands)


@pytest.mark.asyncio
async def test_download_url_uses_content_disposition_when_target_is_unknown():
    sftp = _ArchiveSftp()
    sftp.header = (
        b"HTTP/1.1 200 OK\r\nContent-Disposition: attachment; filename*=UTF-8''addon.zip\r\n\r\n"
    )
    manager = _manager(sftp)
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    callback = lambda _path: None

    success, error = await manager.download_url_to_file(
        "https://example.com/download",
        None,
        _server(),
        destination_path="/srv/cs2",
        resolved_target_callback=callback,
    )

    assert (success, error) == (True, "")
    assert any("addon.zip" in call[1] for call in sftp.calls if call[0] == "lstat")


@pytest.mark.asyncio
async def test_extract_archive_success_and_sync_progress_callback():
    sftp = _ArchiveSftp()
    manager = _manager(sftp)
    manager._inspect_archive_connected = AsyncMock(
        return_value=(True, {"folders": [], "has_backslash_separators": False}, "")
    )
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    progress: list[str] = []

    success, error = await manager.extract_archive(
        "/srv/cs2/plugin.zip",
        "/srv/cs2/addons",
        _server(),
        progress_callback=progress.append,
    )

    assert (success, error) == (True, "")
    assert progress == [
        "Inspecting archive",
        "Preparing staging directory",
        "Extracting archive",
        "Merging extracted files",
        "Extraction complete",
    ]
    assert any("unzip" in call.args[0] for call in manager.execute_command.await_args_list)
    assert any("--no-clobber" in call.args[0] for call in manager.execute_command.await_args_list)


@pytest.mark.asyncio
async def test_extract_archive_overwrite_with_source_folder():
    sftp = _ArchiveSftp()
    manager = _manager(sftp)
    manager._inspect_archive_connected = AsyncMock(
        return_value=(True, {"folders": ["addons"], "has_backslash_separators": False}, "")
    )
    manager._normalize_archive_member = lambda *_args, **_kwargs: ("addons", None)
    manager.execute_command = AsyncMock(return_value=(True, "", ""))

    success, error = await manager.extract_archive(
        "/srv/cs2/plugin.zip",
        "/srv/cs2/addons",
        _server(),
        overwrite=True,
        source_folder="addons/",
        strip_source_folder=True,
    )

    assert (success, error) == (True, "")
    commands = [call.args[0] for call in manager.execute_command.await_args_list]
    assert any("test -d" in command for command in commands)
    assert any("--remove-destination" in command for command in commands)


@pytest.mark.asyncio
async def test_download_url_existing_target_redirect_and_remote_failures():
    class ExistingSftp(_ArchiveSftp):
        def __init__(self, target_type=FILEXFER_TYPE_REGULAR):
            super().__init__()
            self.target_type = target_type

        async def lstat(self, path):
            if path == "/srv/cs2/plugin.zip":
                return SimpleNamespace(type=self.target_type, size=10)
            return await super().lstat(path)

    manager = _manager(ExistingSftp())
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    assert await manager.download_url_to_file("https://x/a.zip", "/srv/cs2/plugin.zip", _server()) == (
        False,
        "Target file already exists. Enable overwrite to replace it.",
    )
    manager = _manager(ExistingSftp(target_type=2))
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    assert "regular file" in (await manager.download_url_to_file(
        "https://x/a.zip", "/srv/cs2/plugin.zip", _server(), overwrite=True
    ))[1]

    sftp = _ArchiveSftp()
    manager = _manager(sftp)
    async def failed_download(command, **_kwargs):
        if "curl" in command:
            return False, "", "secret"
        return True, "", ""

    manager.execute_command = failed_download
    assert "Download failed" in (await manager.download_url_to_file(
        "https://x/a.zip", "/srv/cs2/plugin.zip", _server()
    ))[1]

    class RedirectSftp(_ArchiveSftp):
        def __init__(self):
            super().__init__()
            self.headers = [
                b"HTTP/1.1 302 Found\r\nLocation: https://example.com/final.zip\r\n\r\n",
                b"HTTP/1.1 200 OK\r\nContent-Type: application/zip\r\n\r\n",
            ]

        async def lstat(self, path):
            if path.endswith(".headers"):
                return SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=1024)
            return await super().lstat(path)

        def open(self, path, *_args, **_kwargs):
            return _Context(_HeaderFile(self.headers.pop(0)))

    sftp = RedirectSftp()
    manager = _manager(sftp)
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    assert await manager.download_url_to_file(
        "https://example.com/start.zip", "/srv/cs2/final.zip", _server()
    ) == (True, "")


@pytest.mark.asyncio
async def test_download_url_metadata_and_redirect_validation_errors():
    class BadMeta(_ArchiveSftp):
        async def lstat(self, path):
            if path.endswith(".headers"):
                return SimpleNamespace(type=FILEXFER_TYPE_DIRECTORY, size=0)
            return await super().lstat(path)

    manager = _manager(BadMeta())
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    assert "metadata is not a regular" in (await manager.download_url_to_file(
        "https://example.com/a.zip", "/srv/cs2/a.zip", _server()
    ))[1]
    manager = _manager(_ArchiveSftp())
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    manager._validate_remote_download_url = lambda _url: (None, "bad url")
    assert await manager.download_url_to_file("https://example.com/a.zip", "/srv/cs2/a.zip", _server()) == (
        False,
        "bad url",
    )


@pytest.mark.asyncio
async def test_extract_archive_uses_tar_sevenzip_and_single_file_tools():
    for archive_type, tool in (("tar.gz", "/bin/tar"), ("7z", "/bin/7zz"), ("gz", "/bin/gzip")):
        sftp = _ArchiveSftp()
        manager = _manager(sftp)
        manager.archive_type_from_path = lambda _path, value=archive_type: value
        manager._inspect_archive_connected = AsyncMock(
            return_value=(True, {"folders": [], "has_backslash_separators": False}, "")
        )

        async def find_tool(candidates, selected=tool):
            return selected

        manager._find_remote_tool = AsyncMock(side_effect=find_tool)
        manager.execute_command = AsyncMock(return_value=(True, "", ""))
        assert await manager.extract_archive(
            f"/srv/cs2/archive.{archive_type}", "/srv/cs2/out", _server(), overwrite=True
        ) == (True, "")
        assert any(tool in call.args[0] for call in manager.execute_command.await_args_list)


@pytest.mark.asyncio
async def test_extract_archive_rejects_special_entries_and_tool_failures():
    sftp = _ArchiveSftp()
    manager = _manager(sftp)
    manager._inspect_archive_connected = AsyncMock(
        return_value=(True, {"folders": [], "has_backslash_separators": False}, "")
    )
    async def special_entry(command, **_kwargs):
        if "-type l" in command:
            return True, "/tmp/link", ""
        return True, "", ""

    manager.execute_command = special_entry
    assert "link or special" in (await manager.extract_archive(
        "/srv/cs2/a.zip", "/srv/cs2/out", _server()
    ))[1]

    manager = _manager(_ArchiveSftp())
    manager._inspect_archive_connected = AsyncMock(
        return_value=(True, {"folders": [], "has_backslash_separators": False}, "")
    )
    manager._find_remote_tool = AsyncMock(return_value=None)
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    assert "install unzip" in (await manager.extract_archive(
        "/srv/cs2/a.zip", "/srv/cs2/out", _server()
    ))[1]
