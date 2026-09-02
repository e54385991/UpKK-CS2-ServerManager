"""Connection operations for SSHManager."""

# ruff: noqa: F403,F405

import time

from services.bounded_output import BoundedLineBuffer
from services.ssh.stream_progress import iter_ssh_progress_lines
from services.ssh.text import decode_remote_text

from .common import *


def _legacy_connection_module():
    from . import connection as legacy_connection

    return legacy_connection


def _schedule_legacy_status_update(server_id: int, success: bool) -> None:
    _legacy_connection_module()._schedule_status_update(server_id, success)


class ConnectionRuntimeMixin(SSHMixinBase):
    """Focused SSH connection capability."""

    STREAMING_OUTPUT_MAX_BYTES = 2 * 1024 * 1024
    SUPPORTED_ARCHIVE_FORMATS_LABEL = (
        ".zip, .7z, .rar, .tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tbz, "
        ".tar.xz, .txz, .tar.zst, .tzst, .tar.lzma, .tlz, .gz, .bz2, .xz, "
        ".zst, .zstd, .lzma"
    )
    TAR_ARCHIVE_TYPES = frozenset({"tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lzma"})
    SEVEN_ZIP_ARCHIVE_TYPES = frozenset({"7z", "rar"})
    SINGLE_FILE_ARCHIVE_TYPES = frozenset({"gz", "bz2", "xz", "zst", "lzma"})
    ARCHIVE_TYPES_ALLOW_BACKSLASH = TAR_ARCHIVE_TYPES | SEVEN_ZIP_ARCHIVE_TYPES
    REMOTE_DOWNLOAD_FILENAME_MAX_BYTES = 255
    REMOTE_DOWNLOAD_URL_MAX_LENGTH = 4096
    REMOTE_DOWNLOAD_REDIRECT_CODES = frozenset((301, 302, 303, 307, 308))
    METAMOD_DOWNLOADS_URL = "https://www.sourcemm.net/downloads.php?branch=dev"
    METAMOD_GITHUB_RELEASES_API = (
        "https://api.github.com/repos/alliedmodders/metamod-source/releases?per_page=50"
    )
    METAMOD_LINUX_DOWNLOAD_PATTERN = (
        r"https://github\.com/alliedmodders/metamod-source/releases/download/"
        r"2\.0\.0\.[0-9]+/mmsource-2\.0\.0-git[0-9]+-linux\.tar\.gz"
    )

    async def connect(self, server: Server) -> Tuple[bool, str]:
        """
        Connect to server via SSH (uses connection pool by default)
        Returns: (success: bool, message: str)
        """
        self.current_server = server

        if self.use_pool:
            # Use connection pool
            (
                success,
                conn,
                msg,
            ) = await _legacy_connection_module().ssh_connection_pool.get_connection(server)
            self.conn = conn

            # Track SSH connection status in background (don't block on DB update)
            _schedule_legacy_status_update(server.id, success)

            return success, msg
        else:
            # Direct connection (legacy mode)
            try:
                if server.is_password_auth:
                    # Password authentication
                    self.conn = await asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        password=server.ssh_password,
                        known_hosts=None,
                        connect_timeout=15,
                    )
                elif server.is_key_auth:
                    # Key file authentication
                    self.conn = await asyncssh.connect(
                        host=server.host,
                        port=server.ssh_port,
                        username=server.ssh_user,
                        client_keys=[server.ssh_key_path],
                        known_hosts=None,
                        connect_timeout=15,
                    )
                else:
                    return False, f"Unsupported auth type: {server.auth_type}"

                # Track successful connection
                _schedule_legacy_status_update(server.id, True)

                return True, "Connected successfully"
            except asyncssh.PermissionDenied:
                _schedule_legacy_status_update(server.id, False)
                return False, "Authentication failed"
            except asyncio.TimeoutError:
                _schedule_legacy_status_update(server.id, False)
                return (
                    False,
                    "SSH connection timeout - server may be unreachable or too slow to respond",
                )
            except asyncssh.Error as e:
                _schedule_legacy_status_update(server.id, False)
                return False, f"SSH error: {str(e)}"
            except Exception as e:
                _schedule_legacy_status_update(server.id, False)
                return False, f"Connection error: {str(e)}"

    async def execute_command(self, command: str, timeout: int = 30) -> Tuple[bool, str, str]:
        """
        Execute command on remote server
        Returns: (success: bool, stdout: str, stderr: str)
        """
        conn = self.conn
        if not conn:
            return False, "", "Not connected"

        async def _do_execute():
            assert conn is not None
            result = await asyncio.wait_for(
                conn.run(command, check=False, encoding=None),
                timeout=timeout,
            )

            stdout_text = decode_remote_text(result.stdout)
            stderr_text = decode_remote_text(result.stderr)
            exit_status = result.exit_status

            return exit_status == 0, stdout_text, stderr_text

        try:
            return await _do_execute()
        except asyncio.TimeoutError:
            return False, "", "Command timeout"
        except (asyncssh.ConnectionLost, asyncssh.DisconnectError, asyncssh.ChannelOpenError) as e:
            # SSH connection errors that can be fixed by reconnection
            error_msg = str(e)
            logger.warning(f"[SSH Manager] SSH connection error in execute_command: {error_msg}")
            if self.use_pool and self.current_server:
                try:
                    success, conn, reconnect_msg = await self._reconnect_current_pooled_connection(
                        self.current_server
                    )
                    if success:
                        logger.info(
                            "[SSH Manager] Reconnection successful, retrying execute_command"
                        )
                        # Retry once after reconnection
                        return await _do_execute()
                    else:
                        logger.error(f"[SSH Manager] Reconnection failed: {reconnect_msg}")
                        return False, "", f"连接失败 | Connection failed: {reconnect_msg}"
                except Exception as retry_e:
                    logger.error(
                        f"[SSH Manager] Retry execute_command after reconnection failed: {str(retry_e)}"
                    )
                    return (
                        False,
                        "",
                        f"操作失败（重连后重试仍失败）| Operation failed after reconnection: {str(retry_e)}",
                    )
            return False, "", str(e)
        except Exception as e:
            return False, "", str(e)

    async def create_interactive_process(self, command: str | None = None):
        """Open a PTY as raw bytes so game/tmux output cannot raise UnicodeDecodeError."""
        if not self.conn:
            raise RuntimeError("Not connected")
        kwargs = {"term_type": "xterm-256color", "encoding": None}
        try:
            if command:
                return await self.conn.create_process(command, **kwargs)
            return await self.conn.create_process(**kwargs)
        except TypeError:
            if command:
                return await self.conn.create_process(command, term_type="xterm-256color")
            return await self.conn.create_process(term_type="xterm-256color")

    async def _retry_stream_after_error(
        self, error: Exception, stdout_lines, stderr_lines, execute, timeout: int
    ) -> Tuple[bool, str, str]:
        error_msg = str(error)
        logger.warning(
            "[SSH Manager] SSH connection error in execute_command_streaming: %s", error_msg
        )
        if not (self.use_pool and self.current_server):
            return False, stdout_lines.text(), error_msg
        try:
            success, _conn, reconnect_msg = await self._reconnect_current_pooled_connection(
                self.current_server
            )
            if not success:
                logger.error("[SSH Manager] Reconnection failed: %s", reconnect_msg)
                return False, stdout_lines.text(), f"连接失败 | Connection failed: {reconnect_msg}"
            logger.info("[SSH Manager] Reconnection successful, retrying execute_command_streaming")
            stdout_lines.clear()
            stderr_lines.clear()
            return await asyncio.wait_for(execute(), timeout=timeout)
        except Exception as retry_error:
            logger.error("[SSH Manager] Retry execute_command_streaming failed: %s", retry_error)
            return (
                False,
                stdout_lines.text(),
                f"操作失败（重连后重试仍失败）| Operation failed after reconnection: {retry_error}",
            )

    async def execute_command_streaming(
        self, command: str, output_callback=None, timeout: int = 1800
    ) -> Tuple[bool, str, str]:
        """
        Execute command on remote server with real-time output streaming

        Args:
            command: Command to execute
            output_callback: Optional async callback function to receive output lines in real-time
            timeout: Command timeout in seconds (default: 1800 = 30 minutes)

        Returns: (success: bool, stdout: str, stderr: str)
        """
        conn = self.conn
        if not conn:
            return False, "", "Not connected"

        stdout_lines = BoundedLineBuffer(self.STREAMING_OUTPUT_MAX_BYTES)
        stderr_lines = BoundedLineBuffer(self.STREAMING_OUTPUT_MAX_BYTES)

        async def _execute():
            assert conn is not None
            # PTY makes SteamCMD flush \\r progress instead of buffering a
            # whole download with no newlines.
            try:
                process = await conn.create_process(command, term_type="xterm", encoding=None)
            except TypeError:
                try:
                    process = await conn.create_process(command, encoding=None)
                except TypeError:
                    process = await conn.create_process(command)

            # Helper to send output via callback
            async def send_output(line: str):
                if output_callback:
                    if asyncio.iscoroutinefunction(output_callback):
                        await output_callback(line)
                    else:
                        output_callback(line)

            # Read stdout and stderr concurrently
            async def read_stream(stream, lines_list, prefix=""):
                """Read from a stream and collect lines, including CR progress."""
                last_line = ""
                last_emit_at = 0.0
                try:
                    async for line in iter_ssh_progress_lines(stream):
                        lines_list.append(line)
                        now = time.monotonic()
                        if line != last_line or now - last_emit_at >= 2.0:
                            last_line = line
                            last_emit_at = now
                            await send_output(f"{prefix}{line}" if prefix else line)
                except Exception as e:
                    await send_output(f"Stream read error: {str(e)}")

            # Read both stdout and stderr concurrently
            await asyncio.gather(
                read_stream(process.stdout, stdout_lines),
                read_stream(process.stderr, stderr_lines, "[STDERR] "),
                return_exceptions=True,
            )

            # AsyncSSH returns an SSHCompletedProcess here, not the numeric
            # exit status itself. Comparing that result object directly with
            # zero makes every streaming command look like a failure.
            completed = await process.wait()

            stdout_text = stdout_lines.text()
            stderr_text = stderr_lines.text()

            return completed.exit_status == 0, stdout_text, stderr_text

        try:
            return await asyncio.wait_for(_execute(), timeout=timeout)
        except asyncio.TimeoutError:
            return False, stdout_lines.text(), "Command timeout"
        except (asyncssh.ConnectionLost, asyncssh.DisconnectError, asyncssh.ChannelOpenError) as e:
            return await self._retry_stream_after_error(
                e, stdout_lines, stderr_lines, _execute, timeout
            )
        except Exception as e:
            return False, stdout_lines.text(), f"Execution error: {str(e)}"

    async def execute_sudo_command(
        self, command: str, sudo_password: Optional[str] = None, timeout: int = 30
    ) -> Tuple[bool, str, str]:
        """
        Execute command with sudo on remote server
        Returns: (success: bool, stdout: str, stderr: str)
        """
        if not self.conn:
            return False, "", "Not connected"

        try:
            sudo_command = f"sudo -n -- sh -c {shlex.quote(command)}"
            if sudo_password:
                sudo_command = (
                    f"printf '%s\\n' {shlex.quote(sudo_password)} | "
                    f"sudo -S -- sh -c {shlex.quote(command)}"
                )

            result = await asyncio.wait_for(
                self.conn.run(sudo_command, check=False, encoding=None),
                timeout=timeout,
            )

            stdout_text = decode_remote_text(result.stdout)
            stderr_text = decode_remote_text(result.stderr)
            exit_status = result.exit_status

            return exit_status == 0, stdout_text, stderr_text
        except asyncio.TimeoutError:
            return False, "", "Command timeout"
        except Exception as e:
            return False, "", str(e)

    async def disconnect(self):
        """Release or close SSH connection"""
        if self.conn:
            if self.use_pool and self.current_server:
                # Release connection back to pool
                await _legacy_connection_module().ssh_connection_pool.release_connection(
                    self.current_server, self.conn
                )
            else:
                # Direct connection - close it
                self.conn.close()
                await self.conn.wait_closed()

            self.conn = None
            self.current_server = None
