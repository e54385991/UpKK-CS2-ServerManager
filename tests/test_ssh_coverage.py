"""覆盖 SSH facade 的本地文件、连接和启动安全分支。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest
from asyncssh.constants import FILEXFER_TYPE_DIRECTORY, FILEXFER_TYPE_REGULAR, FILEXFER_TYPE_SYMLINK

from services.ssh_manager import SSHManager


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class _File:
    def __init__(self, content=b""):
        self.content = content
        self.writes = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self, *_args):
        return self.content

    async def write(self, content):
        self.writes.append(content)


class _BasicSftp:
    def __init__(self, entries=None, attrs=None, content=b"text"):
        self.entries = entries or []
        self.attrs = attrs
        self.content = content
        self.file = _File(content)
        self.calls = []
        self.stat_error = None

    async def scandir(self, _path):
        for entry in self.entries:
            yield entry

    async def stat(self, _path):
        if self.stat_error:
            raise self.stat_error
        return self.attrs

    async def lstat(self, _path):
        if self.stat_error:
            raise self.stat_error
        return self.attrs

    def open(self, *_args, **_kwargs):
        self.calls.append(("open", _args, _kwargs))
        return _Context(self.file)

    async def makedirs(self, path):
        self.calls.append(("makedirs", path))

    async def rmtree(self, path):
        self.calls.append(("rmtree", path))

    async def remove(self, path):
        self.calls.append(("remove", path))

    async def rename(self, old, new):
        self.calls.append(("rename", old, new))


class _Connection:
    def __init__(self, sftp):
        self.sftp = sftp

    def start_sftp_client(self):
        return _Context(self.sftp)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.waited = True


def _server(**overrides):
    values = dict(
        id=8,
        user_id=2,
        name="test",
        game_directory="/srv/cs2",
        host="example.test",
        ssh_port=22,
        ssh_user="steam",
        auth_type="password",
        is_password_auth=True,
        is_key_auth=False,
        ssh_password="pw",
        ssh_key_path=None,
        session_manager="tmux",
        game_port=27015,
        client_port=None,
        default_map="de_dust2",
        game_mode="competitive",
        game_type=None,
        additional_parameters="",
        max_players=32,
        server_name="Test",
        ip_address=None,
        steam_account_token=None,
        server_password=None,
        rcon_password=None,
        tv_enable=False,
        tv_port=None,
        backend_url=None,
        api_key=None,
        cpu_affinity=None,
        sudo_password=None,
        use_panel_proxy=False,
        github_proxy=None,
        is_admin=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _manager_with_sftp(sftp):
    manager = SSHManager(use_pool=False)
    manager.conn = _Connection(sftp)
    return manager


@pytest.mark.asyncio
async def test_basic_file_operations_list_read_write_delete_and_rename():
    entries = [
        SimpleNamespace(
            filename="Z.cfg", attrs=SimpleNamespace(type=0, size=4, mtime=2, permissions=0o644)
        ),
        SimpleNamespace(
            filename="addons",
            attrs=SimpleNamespace(
                type=FILEXFER_TYPE_DIRECTORY, size=None, mtime=None, permissions=None
            ),
        ),
        SimpleNamespace(
            filename="link",
            attrs=SimpleNamespace(type=FILEXFER_TYPE_SYMLINK, size=1, mtime=1, permissions=0o777),
        ),
    ]
    sftp = _BasicSftp(entries, SimpleNamespace(type=0, size=4), b"hello")
    manager = _manager_with_sftp(sftp)
    server = _server()

    ok, files, error = await manager.list_directory("/srv/cs2", server)
    assert ok and not error
    assert [item["name"] for item in files] == ["addons", "link", "Z.cfg"]
    assert files[1]["is_symlink"] is True

    ok, text, error = await manager.read_file("/srv/cs2/config.cfg", server)
    assert (ok, text, error) == (True, "hello", "")
    sftp.content = b"\xff"
    sftp.file.content = b"\xff"
    ok, text, _ = await manager.read_file("/srv/cs2/binary", server)
    assert ok and text == "ÿ"

    sftp.attrs = SimpleNamespace(type=0, size=99)
    ok, _, error = await manager.read_file("/srv/cs2/large", server, max_size=10)
    assert ok is False and "too large" in error
    sftp.attrs = SimpleNamespace(type=0, size=1)
    sftp.stat_error = asyncssh.SFTPNoSuchFile("missing")
    ok, error = await manager.write_file("/srv/cs2/cfg/a.cfg", "x", server)
    assert ok and not error
    sftp.stat_error = None
    ok, error = await manager.write_file("/srv/cs2/cfg/a.cfg", "x", server)
    assert ok and error == ""

    sftp.attrs = SimpleNamespace(type=FILEXFER_TYPE_DIRECTORY)
    ok, error = await manager.delete_path("/srv/cs2/addons", server)
    assert ok and ("rmtree", "/srv/cs2/addons") in sftp.calls
    sftp.attrs = SimpleNamespace(type=FILEXFER_TYPE_REGULAR)
    ok, error = await manager.delete_path("/srv/cs2/a.cfg", server)
    assert ok and ("remove", "/srv/cs2/a.cfg") in sftp.calls
    ok, error = await manager.rename_path("/srv/cs2/a", "/srv/cs2/b", server)
    assert ok and ("rename", "/srv/cs2/a", "/srv/cs2/b") in sftp.calls


@pytest.mark.asyncio
async def test_basic_file_operation_connection_validation_and_copy(monkeypatch):
    server = _server()
    manager = SSHManager(use_pool=False)
    manager.connect = AsyncMock(return_value=(False, "offline"))
    assert await manager.list_directory("/srv/cs2", server) == (
        False,
        [],
        "Connection failed: offline",
    )
    assert await manager.read_file("/srv/cs2/a", server) == (
        False,
        "",
        "Connection failed: offline",
    )
    assert await manager.write_file("/srv/cs2/a", "x", server) == (
        False,
        "Connection failed: offline",
    )

    sftp = _BasicSftp(attrs=SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=1))
    manager = _manager_with_sftp(sftp)
    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    manager._remote_exists = AsyncMock(side_effect=[True, False])
    ok, path, error = await manager.copy_into_directory("/srv/cs2/a.cfg", "/srv/cs2/dest", server)
    assert (ok, path, error) == (True, "/srv/cs2/dest/a copy.cfg", "")
    manager.execute_command.assert_awaited_once()

    assert await manager.copy_into_directory("/srv/cs2", "/srv/cs2", server) == (
        False,
        "",
        "Cannot copy a folder into itself",
    )
    manager._remote_exists = AsyncMock(return_value=True)
    ok, _, error = await manager.copy_into_directory("/srv/cs2/a", "/srv/cs2/d", server)
    assert ok is False and "collisions" in error
    manager.validate_path_within_base = AsyncMock(return_value=(False, "outside"))
    ok, _, error = await manager.copy_into_directory("/srv/cs2/a", "/srv/cs2/d", server)
    assert (ok, error) == (False, "outside")

    manager.validate_path_within_base = AsyncMock(return_value=(True, ""))
    manager.execute_command = AsyncMock(return_value=(False, "", "cp failed"))
    manager._remote_exists = AsyncMock(return_value=False)
    ok, _, error = await manager.copy_into_directory("/srv/cs2/a", "/srv/cs2/d", server)
    assert ok is False and "cp failed" in error
    manager.conn = None
    assert await manager._remote_exists("/srv/cs2/a") is False


@pytest.mark.asyncio
async def test_file_transfer_and_stream_operations(tmp_path):
    class TransferSftp(_BasicSftp):
        async def open(self, *_args, **_kwargs):
            return _Context(_File())

        async def makedirs(self, path, **_kwargs):
            self.calls.append(("makedirs", path))

        async def put(self, *args):
            self.calls.append(("put", args))

        async def get(self, *args):
            self.calls.append(("get", args))

    local = tmp_path / "source.txt"
    local.write_text("hello", encoding="utf-8")
    sftp = TransferSftp(attrs=SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=5))
    manager = _manager_with_sftp(sftp)
    server = _server()
    ok, error = await manager.upload_file(str(local), "/srv/cs2/nested/a.txt", server)
    assert ok and not error
    ok, error = await manager.download_file(
        "/srv/cs2/a.txt", str(tmp_path / "out" / "a.txt"), server
    )
    assert ok and not error
    ok, size, error = await manager.get_file_size("/srv/cs2/a.txt", server)
    assert (ok, size, error) == (True, 5, "")
    sftp.attrs = SimpleNamespace(type=FILEXFER_TYPE_DIRECTORY, size=0)
    ok, size, error = await manager.get_file_size("/srv/cs2/addons", server)
    assert (ok, size, error) == (False, None, "Cannot download a directory")

    class StreamFile(_File):
        def __init__(self):
            super().__init__()
            self.chunks = iter(["part", b"end", b""])

        async def read(self, _size):
            return next(self.chunks)

    stream_sftp = _BasicSftp(attrs=SimpleNamespace(type=FILEXFER_TYPE_REGULAR, size=5))
    stream_sftp.file = StreamFile()
    manager.conn = _Connection(stream_sftp)
    chunks = [chunk async for chunk in manager.stream_file("/srv/cs2/a", server)]
    assert chunks == [b"part", b"end"]
    manager.conn = _Connection(sftp)

    progress = []

    async def on_progress(done, total):
        progress.append((done, total))

    ok, error = await manager.upload_file_with_progress(
        str(local), "/srv/cs2/nested/b.txt", server, on_progress
    )
    assert ok and not error and progress[-1] == (5, 5)


@pytest.mark.asyncio
async def test_connection_runtime_and_streaming_paths(monkeypatch):
    from services.ssh import connection_runtime

    server = _server()
    manager = SSHManager(use_pool=False)
    status_calls = []

    def status(*args):
        status_calls.append(args)

    monkeypatch.setattr(connection_runtime, "_schedule_legacy_status_update", status)
    connection = SimpleNamespace(
        close=lambda: setattr(connection, "closed", True),
        wait_closed=AsyncMock(side_effect=lambda: setattr(connection, "waited", True)),
    )
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(connection_runtime.asyncssh, "connect", connect)
    ok, message = await manager.connect(server)
    assert (ok, message) == (True, "Connected successfully")
    assert connect.await_args.kwargs["password"] == "pw"
    assert status_calls == [(8, True)]

    key_server = _server(is_password_auth=False, is_key_auth=True, ssh_key_path="/tmp/key")
    await manager.disconnect()
    assert connection.closed and connection.waited
    await manager.connect(key_server)
    assert connect.await_args.kwargs["client_keys"] == ["/tmp/key"]
    unsupported = _server(is_password_auth=False, is_key_auth=False)
    manager = SSHManager(use_pool=False)
    assert await manager.connect(unsupported) == (False, "Unsupported auth type: password")

    manager.conn = None
    assert await manager.execute_command("true") == (False, "", "Not connected")

    class RunConn:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(stdout=b"out\xff", stderr=b"err", exit_status=0)

        async def create_process(self, *_args, **_kwargs):
            return SimpleNamespace()

    manager.conn = RunConn()
    assert await manager.execute_command("echo") == (True, "out�", "err")
    manager.conn.run = AsyncMock(side_effect=asyncio.TimeoutError())
    assert await manager.execute_command("slow") == (False, "", "Command timeout")
    with pytest.raises(RuntimeError):
        manager.conn = None
        await manager.create_interactive_process("x")
    manager.conn = RunConn()
    assert await manager.create_interactive_process("x") is not None
    assert await manager.execute_sudo_command("id", "secret") == (True, "out�", "err")


@pytest.mark.asyncio
async def test_download_connection_security_helpers_and_reconnect(monkeypatch):
    server = _server()
    assert SSHManager.archive_type_from_path("a.TAR.GZ") == "tar.gz"
    assert SSHManager.archive_type_from_path("a.unknown") is None
    assert (
        SSHManager._filename_from_content_disposition("attachment; filename=demo.zip") == "demo.zip"
    )
    assert (
        SSHManager._filename_from_content_disposition("attachment; filename*=UTF-8''demo%20x.zip")
        == "demo x.zip"
    )
    assert SSHManager._validate_download_filename("../bad.zip")[0] is None
    assert SSHManager._validate_download_filename("good.zip") == ("good.zip", "")
    assert SSHManager._filename_from_download_response(
        "HTTP/1.1 200 OK\r\nContent-Disposition: attachment; filename*=UTF-8''answer.zip\r\n\r\n",
        "https://example.test/no-name",
    ) == ("answer.zip", "")
    assert SSHManager._validate_remote_download_url("https://example.test/file.zip")[1] == ""
    for bad in (
        "",
        "ftp://example.test/a",
        "http://127.0.0.1/a",
        "https://example.test/a#frag",
        "https://u:p@example.test/a",
    ):
        assert SSHManager._validate_remote_download_url(bad)[0] is None
    assert SSHManager._curl_resolve_entry("host", 443, "1.2.3.4") == "host:443:1.2.3.4"
    assert SSHManager._curl_resolve_entry("::1", 443, "2001:db8::1") == "[::1]:443:[2001:db8::1]"
    status, headers, error = SSHManager._download_response_metadata(
        "HTTP/1.1 200 OK\r\nX-Test: a\r\n b\r\n\r\n"
    )
    assert (status, headers["x-test"], error) == (200, "a b", "")
    assert SSHManager._download_response_metadata("garbage")[0] is None
    assert (
        SSHManager._redirect_url_from_response(
            "HTTP/1.1 302 Found\r\nLocation: /next.zip\r\n\r\n", "https://example.test/a.zip"
        )[0]
        == "https://example.test/next.zip"
    )
    assert (
        SSHManager._redirect_url_from_response(
            "HTTP/1.1 302 Found\r\n\r\n", "https://example.test/a.zip"
        )[1]
        is True
    )
    assert (
        SSHManager._redirect_url_from_response(
            "HTTP/1.1 404 Not Found\r\n\r\n", "https://example.test/a.zip"
        )[1]
        is False
    )
    assert "[redacted URL]" in SSHManager._redact_download_error(
        "failed https://x.test/a?sig=secret"
    )
    assert (
        SSHManager._apply_github_download_proxy("https://github.com/a/b", " https://proxy.test/ ")
        == "https://proxy.test/https://github.com/a/b"
    )

    manager = SSHManager(use_pool=False)
    manager.execute_command = AsyncMock(return_value=(True, "1.2.3.4\n2001:4860:4860::8888\n", ""))
    assert await manager._resolve_public_download_address("example.test", "getent") == (
        "1.2.3.4",
        "",
    )
    resolved = await manager._resolve_public_download_address("127.0.0.1", "getent")
    assert resolved[0] is None
    manager.execute_command = AsyncMock(return_value=(False, "", "error"))
    assert (await manager._resolve_public_download_address("example.test", "getent"))[0] is None

    pool = SimpleNamespace(
        reconnect_for_connection=AsyncMock(return_value=(True, "new", "")),
        release_connection=AsyncMock(),
    )
    monkeypatch.setattr("services.ssh.connection_download._legacy_connection_pool", lambda: pool)
    manager.conn = "old"
    assert await manager._reconnect_current_pooled_connection(server) == (True, "new", "")
    assert manager.conn == "new"
    manager.use_pool = False
    with pytest.raises(Exception, match="broken"):
        await manager._handle_sftp_error_with_reconnect(
            Exception("broken"), server, "read", AsyncMock()
        )


@pytest.mark.asyncio
async def test_selfcheck_summary_and_session_preflight(monkeypatch):
    manager = SSHManager(use_pool=False)
    server = _server()
    progress = []

    async def callback(message):
        progress.append(message)

    commands = {
        "test -f /srv/cs2/cs2/game/bin/linuxsteamrt64/cs2 && echo 'exists'": (True, "exists", ""),
        "chmod +x /srv/cs2/cs2/game/bin/linuxsteamrt64/cs2": (True, "", ""),
        "test -L /home/steam/.steam/sdk64/steamclient.so && test -e /home/steam/.steam/sdk64/steamclient.so && echo 'valid' || echo 'missing'": (
            True,
            "valid",
            "",
        ),
        "test -d /srv/cs2/cs2/game/csgo/addons/metamod && echo 'exists'": (False, "", ""),
        "test -f /srv/cs2/cs2_autorestart.sh && test -x /srv/cs2/cs2_autorestart.sh && echo 'exists'": (
            True,
            "exists",
            "",
        ),
    }

    async def execute(command, **_kwargs):
        return commands.get(command, (True, "", ""))

    manager.execute_command = execute
    ok, message = await manager.perform_server_selfcheck(server, callback)
    assert (ok, message) == (True, "Server self-check passed")
    assert any("No issues" in line for line in progress)
    assert manager._cs2_executable_path(server).endswith("cs2/game/bin/linuxsteamrt64/cs2")
    assert await manager._configured_session_manager_available_connected(server) == (
        True,
        "tmux is available",
    )
    manager.execute_command = AsyncMock(return_value=(False, "", ""))
    assert (await manager._configured_session_manager_available_connected(server))[0] is False
    manager.connect = AsyncMock(return_value=(False, "offline"))
    assert "Connection failed" in (await manager.check_session_manager_available(server))[1]


@pytest.mark.asyncio
async def test_start_server_success_path_is_sanitized(monkeypatch):
    manager = SSHManager(use_pool=False)
    server = _server(
        api_key="apisecret",
        server_password="gamesecret",
        rcon_password="rconsecret",
        steam_account_token="steamsecret",
        tv_enable=True,
        tv_port=27020,
        ip_address="0.0.0.0",
        cpu_affinity="0-3",
    )
    progress = []
    manager.connect = AsyncMock(return_value=(True, "ok"))
    manager.disconnect = AsyncMock()
    manager._cs2_executable_exists_connected = AsyncMock(
        return_value=(True, "/srv/cs2/cs2/game/bin/linuxsteamrt64/cs2")
    )
    manager._configured_session_manager_available_connected = AsyncMock(
        return_value=(True, "tmux is available")
    )
    manager._stop_server_sessions_connected = AsyncMock(return_value=(True, []))
    manager._kill_stray_cs2_processes = AsyncMock()
    manager.perform_server_selfcheck = AsyncMock(return_value=(True, "ok"))
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
    session_results = [["tmux"], ["tmux"], ["tmux"]]

    async def running(_server):
        return session_results.pop(0) if session_results else ["tmux"]

    manager._running_server_session_managers = running
    commands = []

    async def execute(command, **_kwargs):
        commands.append(command)
        if command.startswith("test -f"):
            return True, "exists", ""
        return True, "", ""

    manager.execute_command = execute
    monkeypatch.setattr("services.ssh.game_start.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("services.ssh.game_start.server_monitor", SimpleNamespace())
    monkeypatch.setattr(
        "services.ssh.game_start.steam_inf_service",
        SimpleNamespace(refresh_version_cache=AsyncMock(return_value=(True, "1.2"))),
        raising=False,
    )

    async def callback(message):
        progress.append(message)

    ok, message = await manager.start_server(server, callback)
    assert ok, message
    assert any("***API_KEY***" in line for line in progress)
    assert not any(
        secret in "\n".join(progress)
        for secret in ("apisecret", "gamesecret", "rconsecret", "steamsecret")
    )
    assert manager.disconnect.await_count == 1


@pytest.mark.asyncio
async def test_plugin_adapters_install_success_paths(monkeypatch):
    manager = SSHManager(use_pool=False)
    server = _server(github_proxy="https://proxy.example")
    manager.connect = AsyncMock(return_value=(True, "ok"))
    manager.disconnect = AsyncMock()
    manager._fetch_latest_metamod_url = AsyncMock(
        return_value=(
            True,
            "https://github.com/alliedmodders/metamod-source/releases/download/2.0.0.1/mmsource-2.0.0-git1-linux.tar.gz",
        )
    )
    manager._fetch_github_release_url = AsyncMock(
        return_value=(
            True,
            "https://github.com/Source2ZE/CS2Fixes/releases/download/v1/CS2Fixes-linux.tar.gz",
        )
    )
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
    progress = []

    async def callback(message):
        progress.append(message)

    async def execute(command, **_kwargs):
        if command.startswith("test -f") and "cs2fixes.tar.gz" in command:
            return True, "exists", ""
        if command.startswith("test -f") and "metamod.tar.gz" in command:
            return True, "exists", ""
        if command.startswith("test -f") and "autorestart" in command:
            return True, "exists", ""
        if command.startswith("test -f"):
            return True, "exists", ""
        if command.startswith("test -d") and command.endswith("cs2')"):
            return True, "exists", ""
        if "echo 'extracted'" in command:
            return True, "extracted", ""
        if "echo 'installed'" in command:
            return True, "installed", ""
        if "test -d /srv/cs2/cs2" in command:
            return True, "exists", ""
        if "test -d /srv/cs2/cs2/game/csgo/addons/metamod" in command:
            return True, "exists", ""
        if "test -d /srv/cs2/cs2/game/csgo/addons/cs2fixes" in command:
            return True, "installed", ""
        if "test -d /srv/cs2/cs2/game/csgo/addons/counterstrikesharp" in command:
            return True, "installed", ""
        if "grep -q 'addons/metamod'" in command:
            return True, "found", ""
        if command.startswith("stat "):
            return True, "20000", ""
        if "releases/latest" in command:
            return (
                True,
                "https://github.com/roflmuffin/CounterStrikeSharp/releases/download/v1/counterstrikesharp-with-runtime-linux-v1.zip",
                "",
            )
        return True, "", ""

    manager.execute_command = execute
    ok, message = await manager.install_metamod(server, callback)
    assert (ok, message) == (True, "Metamod:Source installed successfully")
    ok, message = await manager.install_cs2fixes(server, callback)
    assert (ok, message) == (True, "CS2Fixes installed successfully")
    ok, message = await manager.install_counterstrikesharp(server, callback)
    assert (ok, message) == (True, "CounterStrikeSharp installed successfully")
    assert any("proxy" in item.lower() for item in progress)


@pytest.mark.asyncio
async def test_swiftly_install_and_plugin_backup_paths(monkeypatch, tmp_path):
    manager = SSHManager(use_pool=False)
    server = _server()
    manager.connect = AsyncMock(return_value=(True, "ok"))
    manager.disconnect = AsyncMock()
    manager.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
    manager._fetch_github_release_url = AsyncMock(
        return_value=(
            True,
            "https://github.com/swiftly-solution/swiftlys2/releases/download/v1/swiftly-linux-with-runtimes.zip",
        )
    )
    progress = []

    async def callback(message):
        progress.append(message)

    async def execute(command, **_kwargs):
        if "find /tmp/swiftly_install_8/extracted" in command:
            return True, "/tmp/swiftly_install_8/extracted/package/addons\n", ""
        if "echo 'extracted'" in command:
            return True, "extracted", ""
        if "swiftlys2/releases/latest" in command:
            return (
                True,
                "https://github.com/swiftly-solution/swiftlys2/releases/download/v1/swiftly-linux-with-runtimes.zip",
                "",
            )
        if "test -d /srv/cs2/cs2/game/csgo/addons/swiftlys2" in command:
            return True, "installed", ""
        if command.startswith("test -f"):
            return True, "exists", ""
        if command == "command -v unzip":
            return True, "/usr/bin/unzip", ""
        if command.startswith("test -d /srv/cs2/cs2"):
            return True, "exists", ""
        if command.startswith("stat "):
            return True, "20000", ""
        return True, "", ""

    manager.execute_command = execute
    ok, message = await manager.install_swiftly(server, callback)
    assert (ok, message) == (True, "SwiftlyS2 installed successfully")

    backup = SSHManager(use_pool=False)
    backup.connect = AsyncMock(return_value=(True, "ok"))
    backup.disconnect = AsyncMock()
    backup.execute_command_streaming = AsyncMock(return_value=(True, "", ""))
    backup_commands = []

    async def backup_execute(command, **_kwargs):
        backup_commands.append(command)
        if command.startswith("date"):
            return True, "2025-01-01-010101\n", ""
        if "test -d" in command:
            return True, "exists", ""
        if "test -f" in command:
            return True, "exists", ""
        if command.startswith("stat"):
            return True, "2048", ""
        return True, "", ""

    backup.execute_command = backup_execute
    ok, message = await backup.backup_plugins(server, callback)
    assert ok and "backup completed" in message.lower()
    assert backup.last_plugin_backup["size"] == 2048
    assert "tar -czf" in backup.execute_command_streaming.await_args.args[0]
