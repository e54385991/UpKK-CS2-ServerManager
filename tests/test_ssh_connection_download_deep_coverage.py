"""覆盖远端下载连接能力的 URL、响应解析、重连和发布回退逻辑。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ssh_manager import SSHManager


def _server(**overrides):
    values = {"id": 2, "game_directory": "/srv/cs2"}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a.tar.zstd", "tar.zst"),
        ("a.tar.lzma", "tar.lzma"),
        ("a.tgz", "tar.gz"),
        ("a.tbz", "tar.bz2"),
        ("a.txz", "tar.xz"),
        ("a.7z", "7z"),
        ("a.rar", "rar"),
        ("a.tar", "tar"),
        ("a.zstd", "zst"),
        ("a.lzma", "lzma"),
        ("a.gz", "gz"),
        ("a.bz2", "bz2"),
        ("a.xz", "xz"),
    ],
)
def test_archive_and_filename_helpers(path, expected):
    assert SSHManager.archive_type_from_path(path) == expected
    assert SSHManager._validate_download_filename(path) == (path, "")

    assert SSHManager._validate_download_filename(None)[0] is None
    for value in ("", ".", "..", "../a.zip", "a\\b.zip", "a\x00.zip"):
        assert SSHManager._validate_download_filename(value)[0] is None
    assert "too long" in SSHManager._validate_download_filename("é" * 200 + ".zip")[1]
    assert "supported" in SSHManager._validate_download_filename("file.txt")[1]
    assert SSHManager._filename_from_content_disposition("") is None
    assert SSHManager._filename_from_content_disposition("attachment; filename=a.zip") == "a.zip"
    assert SSHManager._filename_from_content_disposition("attachment; filename*=bad''a.zip") == "a.zip"


def test_response_and_url_validation_helpers():
    assert SSHManager._filename_from_download_response(
        "HTTP/1.1 302 Found\nLocation: /x\n\nHTTP/1.1 200 OK\n\n",
        "https://host.example/path/final.zip",
    ) == ("final.zip", "")
    assert SSHManager._filename_from_download_response("garbage", "https://host.example/a.txt")[0] is None
    for value in (
        "https://example.com/a#frag",
        "https://user:pass@example.com/a.zip",
        "https://example.com:0/a.zip",
        "https://example.com:65536/a.zip",
        "https://bad_host.example/a.zip",
        "https://127.0.0.1/a.zip",
        "https://0300.1.1.1/a.zip",
        "file:///tmp/a.zip",
        "https://example.com/\n.zip",
    ):
        assert SSHManager._validate_remote_download_url(value)[0] is None
    assert SSHManager._validate_remote_download_url("https://8.8.8.8/a.zip")[1] == ""
    assert SSHManager._validate_download_hostname("")
    assert SSHManager._validate_download_hostname("bad_host")
    assert SSHManager._validate_download_hostname("example.com") is None

    status, headers, error = SSHManager._download_response_metadata(
        "garbage\n\nHTTP/2 204 No Content\r\nX-Test: one\r\n two\r\n\r\n"
    )
    assert (status, headers, error) == (204, {"x-test": "one two"}, "")
    assert SSHManager._download_response_metadata("invalid")[2]
    assert SSHManager._redirect_url_from_response(
        "HTTP/1.1 200 OK\n\n", "https://example.com/a.zip"
    ) == (None, False, "")
    assert "unsupported" in SSHManager._redirect_url_from_response(
        "HTTP/1.1 304 Not Modified\n\n", "https://example.com/a.zip"
    )[2]
    assert "Location" in SSHManager._redirect_url_from_response(
        "HTTP/1.1 302 Found\n\n", "https://example.com/a.zip"
    )[2]
    assert "not allowed" in SSHManager._redirect_url_from_response(
        "HTTP/1.1 302 Found\nLocation: https://127.0.0.1/x.zip\n\n",
        "https://example.com/a.zip",
    )[2]
    assert "invalid" in SSHManager._redirect_url_from_response(
        "HTTP/1.1 302 Found\nLocation: /x\x00.zip\n\n", "https://example.com/a.zip"
    )[2]
    assert "HTTP status" in SSHManager._redirect_url_from_response(
        "HTTP/1.1 500 Error\n\n", "https://example.com/a.zip"
    )[2]
    assert SSHManager._redact_download_error("") == "Remote command failed"


@pytest.mark.asyncio
async def test_resolve_reconnect_and_sftp_retry_paths(monkeypatch):
    manager = SSHManager(use_pool=False)
    manager.execute_command = AsyncMock(return_value=(True, "2001:4860:4860::8888\n8.8.8.8\n", ""))
    assert await manager._resolve_public_download_address("example.com", "getent") == (
        "8.8.8.8",
        "",
    )
    assert await manager._resolve_public_download_address("8.8.8.8", "getent") == (
        "8.8.8.8",
        "",
    )
    assert "non-public" in (await manager._resolve_public_download_address("10.0.0.1", "getent"))[1]
    manager.execute_command.return_value = (False, "", "dns failed")
    assert "could not be resolved" in (await manager._resolve_public_download_address("example.com", "getent"))[1]
    manager.execute_command.return_value = (True, "not-an-ip\n", "")
    assert "could not be resolved" in (await manager._resolve_public_download_address("example.com", "getent"))[1]
    manager.execute_command.return_value = (True, "10.0.0.1\n", "")
    assert "non-public" in (await manager._resolve_public_download_address("example.com", "getent"))[1]

    pool = SimpleNamespace(
        reconnect_for_connection=AsyncMock(return_value=(True, "new", "")),
        release_connection=AsyncMock(),
    )
    monkeypatch.setattr("services.ssh.connection_download._legacy_connection_pool", lambda: pool)
    manager.use_pool = True
    manager.conn = "old"
    assert await manager._reconnect_current_pooled_connection(_server()) == (True, "new", "")
    pool.release_connection.assert_awaited_once()
    manager.conn = None
    pool.reconnect_for_connection.return_value = (False, None, "pool down")
    assert await manager._reconnect_current_pooled_connection(_server()) == (False, None, "pool down")

    retry = AsyncMock(return_value="retried")
    manager._reconnect_current_pooled_connection = AsyncMock(return_value=(True, "conn", ""))
    assert await manager._handle_sftp_error_with_reconnect(
        Exception("broken pipe"), _server(), "read", retry
    ) == "retried"
    retry.side_effect = RuntimeError("still broken")
    with pytest.raises(Exception, match="重连后重试"):
        await manager._handle_sftp_error_with_reconnect(
            Exception("connection reset"), _server(), "read", retry
        )
    manager._reconnect_current_pooled_connection.return_value = (False, None, "offline")
    with pytest.raises(Exception, match="Connection failed"):
        await manager._handle_sftp_error_with_reconnect(
            Exception("open failed"), _server(), "read", AsyncMock()
        )
    manager.use_pool = False
    with pytest.raises(RuntimeError, match="ordinary"):
        await manager._handle_sftp_error_with_reconnect(
            RuntimeError("ordinary"), _server(), "read", AsyncMock()
        )


@pytest.mark.asyncio
async def test_release_proxy_and_github_fetch_fallbacks(monkeypatch):
    assert SSHManager._apply_github_download_proxy("https://example.com/a", "https://proxy") == "https://example.com/a"
    assert SSHManager._apply_github_download_proxy("https://github.com/a", "") == "https://github.com/a"
    assert SSHManager._apply_github_download_proxy(
        "https://github.com/a", "https://proxy.test/https://github.com/"
    ) == "https://proxy.test/https://github.com/a"
    manager = SSHManager(use_pool=False)
    valid = "https://github.com/alliedmodders/metamod-source/releases/download/2.0.0.1/mmsource-2.0.0-git1-linux.tar.gz"
    manager.execute_command = AsyncMock(return_value=(True, valid + "\n", ""))
    assert await manager._fetch_latest_metamod_url() == (True, valid)
    manager.execute_command = AsyncMock(
        side_effect=[
            (True, "build 42", ""),
            (False, "", ""),
        ]
    )
    assert (await manager._fetch_latest_metamod_url())[0] is False
    manager.execute_command = AsyncMock(
        side_effect=[(False, "", ""), (True, valid, "")]
    )
    assert await manager._fetch_latest_metamod_url() == (True, valid)
    manager.execute_command = AsyncMock(side_effect=[(False, "", ""), (False, "", "")])
    assert (await manager._fetch_latest_metamod_url())[0] is False

    assert (await manager._fetch_github_release_url("bad/repo/x", "x"))[0] is False
    manager.execute_command = AsyncMock(return_value=(True, "https://github.com/a/b/releases/download/v1/a.zip", ""))
    assert await manager._fetch_github_release_url("a/b", "x") == (
        True,
        "https://github.com/a/b/releases/download/v1/a.zip",
    )
    manager.execute_command = AsyncMock(
        side_effect=[
            (False, "", ""),
            (True, "https://github.com/a/b/releases/download/v1/a.zip", ""),
        ]
    )
    assert (await manager._fetch_github_release_url("a/b", "x"))[0] is True
    manager.execute_command = AsyncMock(
        side_effect=[(False, "", ""), (True, "https://other.example/a.zip", ""), (True, "v1.2.3", "")]
    )
    assert "Found tag" in (await manager._fetch_github_release_url("a/b", "x"))[1]
    manager.execute_command = AsyncMock(side_effect=[(False, "", ""), (False, "", ""), (False, "", "")])
    assert "Failed to fetch" in (await manager._fetch_github_release_url("a/b", "x"))[1]
