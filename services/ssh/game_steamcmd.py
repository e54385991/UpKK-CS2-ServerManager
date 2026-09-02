"""Game operations for SSHManager."""

# ruff: noqa: F403,F405

from services.steamcmd_guard import (
    STEAMCMD_FORCE_TERMINATED,
    steamcmd_pgrep_command,
)
from services.steamcmd_retry import (
    clamp_steamcmd_max_retries,
    is_steamcmd_failure_retryable,
    steamcmd_retry_delay_seconds,
)
from services.steamcmd_session import (
    incremental_console_lines,
    latest_console_heartbeat,
    parse_steamcmd_exit_code,
    steamcmd_exit_path,
    wrap_steamcmd_payload,
)

from .common import *


async def _legacy_cancel_requested(server_id):
    from . import game as legacy_game

    return await legacy_game.steamcmd_cancel_requested(server_id)


async def _legacy_resolve_max_retries(user_id):
    from . import game as legacy_game

    return await legacy_game.resolve_steamcmd_max_retries(user_id)


class GameSteamcmdMixin(SSHMixinBase):
    """Focused game lifecycle capability."""

    async def stop_server(self, server: Server) -> Tuple[bool, str]:
        """Stop CS2 server with retry logic to ensure complete termination"""
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

        try:
            all_stopped, managers = await self._stop_server_sessions_connected(
                server,
                retries=5,
            )

            # A pane/session shutdown should terminate its children.  Retain the
            # existing exact-port cleanup as a final guard for detached CS2
            # processes, regardless of which manager owned the session.
            await self._kill_stray_cs2_processes(server)

            if not managers:
                return True, "Server is not running (no screen/tmux session found)"
            if all_stopped:
                return True, (
                    f"Server stopped successfully ({', '.join(managers)} session terminated)"
                )
            return False, "Server failed to stop all screen/tmux sessions"

        except Exception as e:
            return False, f"Stop error: {str(e)}"
        finally:
            await self.disconnect()

    async def _send_progress_if_callback(self, progress_callback, message: str):
        """
        Shared helper to send progress updates if callback is provided

        Args:
            progress_callback: Optional callback for progress messages
            message: Progress message to send
        """
        if progress_callback:
            if asyncio.iscoroutinefunction(progress_callback):
                await progress_callback(message)
            else:
                progress_callback(message)

    async def _steamcmd_session_manager(self, server: Server) -> str | None:
        """Prefer the server's configured manager; fall back to the other one."""
        preferred = normalize_session_manager(server.session_manager)
        for manager in session_manager_order(preferred):
            success, _, _ = await self.execute_command(availability_command(manager), timeout=10)
            if success:
                return manager
        return None

    async def _steamcmd_session_running(
        self, server: Server, manager: str | None = None
    ) -> str | None:
        name = steamcmd_session_name(int(server.id))
        preferred = manager or server.session_manager
        return await find_running_session_manager(self.execute_command, preferred, name, timeout=10)

    async def _read_steamcmd_exit_code(self, server: Server) -> int | None:
        path = steamcmd_exit_path(server.game_directory)
        success, stdout, _ = await self.execute_command(
            f"test -f {shlex.quote(path)} && cat {shlex.quote(path)} || true",
            timeout=10,
        )
        if not success:
            return None
        return parse_steamcmd_exit_code(stdout)

    async def _start_steamcmd_session(
        self, command: str, server: Server, manager: str, name: str, exit_path: str, send_progress
    ) -> tuple[bool, str, str]:
        running = await self._steamcmd_session_running(server, manager)
        if running:
            await send_progress(
                f"Reattached to existing {running} session {name}. SteamCMD was already detached from SSH."
            )
            return True, "", ""
        await self.execute_command(f"rm -f -- {shlex.quote(exit_path)}", timeout=10)
        payload = wrap_steamcmd_payload(command, exit_path)
        await send_progress(
            f"Starting SteamCMD in detached {manager} session {name} (survives SSH reconnect)."
        )
        success, stdout, stderr = await self.execute_command(
            start_session_command(manager, name, payload), timeout=30
        )
        if not success:
            return False, stdout, stderr or "Failed to start SteamCMD session"
        started = await self._steamcmd_session_running(server, manager)
        if started is not None:
            return True, stdout, stderr
        exit_code = await self._read_steamcmd_exit_code(server)
        if exit_code is not None:
            return exit_code == 0, stdout, "" if exit_code == 0 else f"SteamCMD exited {exit_code}"
        return False, stdout, stderr or "SteamCMD session did not start"

    async def _stream_steamcmd_with_heartbeat(
        self,
        command: str,
        server: Server,
        send_progress,
        timeout: int,
    ) -> Tuple[bool, str, str]:
        """Run SteamCMD in a detached tmux/screen session and poll the pane.

        The download must survive SSH reconnect. The CS2 game session
        (``cs2server_<id>``) is separate from ``cs2steamcmd_<id>``.
        """
        manager = await self._steamcmd_session_manager(server)
        if manager is None:
            await send_progress(
                "tmux/screen not found on the host; SteamCMD will run on this "
                "SSH session and can stop if the connection drops."
            )
            return await self.execute_command_streaming(
                command,
                output_callback=send_progress,
                timeout=timeout,
            )

        name = steamcmd_session_name(int(server.id))
        exit_path = steamcmd_exit_path(server.game_directory)
        started, startup_stdout, startup_stderr = await self._start_steamcmd_session(
            command, server, manager, name, exit_path, send_progress
        )
        if not started:
            return False, startup_stdout, startup_stderr

        await send_progress(f"Watching {manager} session {name}; waiting for SteamCMD output…")
        deadline = time.monotonic() + max(int(timeout), 60)
        last_capture = ""
        last_heartbeat = 0.0
        captured_chunks: list[str] = []
        while time.monotonic() < deadline:
            server_id = getattr(server, "id", None)
            if server_id is not None and await _legacy_cancel_requested(server_id):
                await self._kill_steamcmd_processes(server)
                return False, "\n".join(captured_chunks), STEAMCMD_FORCE_TERMINATED

            try:
                active = await self._steamcmd_session_running(server, manager)
                capture_ok, capture, _ = await self.execute_command(
                    capture_console_command(manager, name, lines=120),
                    timeout=15,
                )
                if capture_ok:
                    for line in incremental_console_lines(last_capture, capture or ""):
                        captured_chunks.append(line)
                        await send_progress(line)
                    last_capture = capture or last_capture

                if active is None:
                    exit_code = await self._read_steamcmd_exit_code(server)
                    if exit_code is not None:
                        return (
                            exit_code == 0,
                            "\n".join(captured_chunks),
                            "" if exit_code == 0 else f"SteamCMD exited {exit_code}",
                        )
                    pids = await self._list_steamcmd_pids(server)
                    if not pids:
                        return (
                            False,
                            "\n".join(captured_chunks),
                            "SteamCMD session ended unexpectedly",
                        )
                elif time.monotonic() - last_heartbeat >= 20:
                    last_heartbeat = time.monotonic()
                    # Do not `du` a 70GB tree mid-download — it blocks pane
                    # capture for tens of seconds and the web log looks frozen.
                    pids = await self._list_steamcmd_pids(server)
                    heartbeat = latest_console_heartbeat(capture or last_capture)
                    if heartbeat:
                        await send_progress(heartbeat)
                    await send_progress(f"SteamCMD session {name} running ({len(pids)} pid)")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await send_progress(f"SSH hiccup while watching SteamCMD session {name}: {exc}")
            await asyncio.sleep(2)

        return False, "\n".join(captured_chunks), "Command timeout"

    async def _prepare_steamcmd_retry(
        self, server: Server, attempt: int, max_retries: int, send_progress, progress_callback
    ) -> bool:
        if attempt == 0:
            return True
        still_running = await self._steamcmd_session_running(server)
        if still_running or await self._list_steamcmd_pids(server):
            await send_progress(
                "SteamCMD detached session is still running; resuming the log instead of starting another download."
            )
            return True
        await self._kill_steamcmd_processes(server, progress_callback)
        delay = steamcmd_retry_delay_seconds(attempt, self.STEAMCMD_RETRY_DELAY)
        await send_progress(
            f"⏳ Auto-recover {attempt}/{max_retries} - waiting {int(delay)} seconds before retry..."
        )
        await asyncio.sleep(delay)
        server_id = getattr(server, "id", None)
        if server_id is not None and await _legacy_cancel_requested(server_id):
            await send_progress("✗ SteamCMD force-stop requested during backoff")
            return False
        await send_progress(f"🔄 Starting recovery attempt {attempt}/{max_retries}...")
        return True

    async def _run_steamcmd_attempt(
        self, command: str, server: Server, send_progress, timeout: int
    ):
        try:
            success, stdout, stderr = await self._stream_steamcmd_with_heartbeat(
                command, server, send_progress, timeout=timeout
            )
            if success:
                return True, stdout, stderr, "", False
            return (
                False,
                stdout,
                stderr,
                stderr or stdout or "SteamCMD exited unexpectedly",
                is_steamcmd_failure_retryable(stdout, stderr),
            )
        except asyncio.TimeoutError:
            return False, "", "Command timeout", "Command timeout", True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return False, "", str(exc), f"Unexpected error: {exc}", True

    async def _process_steamcmd_completion(
        self,
        attempt: int,
        max_retries: int,
        send_progress,
        success: bool,
        stdout: str,
        stderr: str,
        retry_reason: str,
        retryable: bool,
        completion_verified: bool | None,
    ):
        if completion_verified is True:
            if not success:
                await send_progress(
                    "✓ SteamCMD exited with an error, but the required deployment file was verified"
                )
            elif attempt > 0:
                await send_progress(
                    f"✓ SteamCMD command succeeded on retry attempt {attempt}/{max_retries}"
                )
            return True, stdout, stderr, retry_reason, retryable
        if completion_verified is False:
            artifact_error = "Required deployment file is missing after SteamCMD exit"
            stderr = f"{stderr}; {artifact_error}" if stderr else artifact_error
            retry_reason = "Required deployment file is missing"
            retryable = success or is_steamcmd_failure_retryable(stdout, stderr)
        elif success:
            if attempt > 0:
                await send_progress(
                    f"✓ SteamCMD command succeeded on retry attempt {attempt}/{max_retries}"
                )
            return True, stdout, stderr, retry_reason, retryable
        return None, stdout, stderr, retry_reason, retryable

    async def _list_steamcmd_pids(self, server: Server) -> list[str]:
        """Return SteamCMD PIDs bound to this server's game directory only."""
        success, stdout, _stderr = await self.execute_command(
            steamcmd_pgrep_command(server.game_directory), timeout=10
        )
        if not success or not stdout.strip():
            return []
        return [pid for pid in stdout.strip().splitlines() if pid.isdigit()]

    async def _kill_steamcmd_processes(self, server: Server, progress_callback=None) -> None:
        """Stop this server's SteamCMD session and matching processes."""
        try:
            name = steamcmd_session_name(int(server.id))
            for manager in session_manager_order(server.session_manager):
                await self.execute_command(force_stop_session_command(manager, name), timeout=10)
            pids = await self._list_steamcmd_pids(server)
            if not pids:
                await self._send_progress_if_callback(
                    progress_callback,
                    f"✓ SteamCMD session {name} stopped",
                )
                return
            await self._send_progress_if_callback(
                progress_callback,
                f"⚠ Found {len(pids)} existing steamcmd process(es) for this server, terminating...",
            )
            for pid in pids:
                await self.execute_command(f"kill -9 {pid} 2>/dev/null || true", timeout=5)
            await asyncio.sleep(0.5)
            leftover = await self._list_steamcmd_pids(server)
            if leftover:
                await self._send_progress_if_callback(
                    progress_callback, "⚠ Some steamcmd processes may still be running"
                )
            else:
                await self._send_progress_if_callback(
                    progress_callback, "✓ All existing steamcmd processes terminated"
                )
        except Exception as e:
            await self._send_progress_if_callback(
                progress_callback, f"Note: Error checking for existing steamcmd processes: {str(e)}"
            )

    async def _kill_stray_cs2_processes(self, server: Server, progress_callback=None) -> None:
        """
        Kill any CS2 server processes left outside managed screen/tmux sessions

        This prevents duplicate processes when starting/updating/validating servers.
        Only kills CS2 processes matching this server's port to avoid affecting other servers.
        Uses word boundary matching to ensure exact port matching (e.g., port 27015 won't match 270).

        Args:
            server: Server instance
            progress_callback: Optional callback for progress messages
        """
        try:
            # Find CS2 processes for this server's port with exact matching
            # Use word boundary \b to prevent matching ports as substrings (e.g., 270 matching in 27015)
            # The pattern 'cs2.*-port\s+{port}\b' ensures we match "-port 27015" but not "-port 270159"
            check_cmd = f"pgrep -f 'cs2.*-port\\s+{server.game_port}\\b' || true"
            success, stdout, stderr = await self.execute_command(check_cmd, timeout=10)

            if stdout.strip():
                pids = stdout.strip().split("\n")
                await self._send_progress_if_callback(
                    progress_callback,
                    f"⚠ Found {len(pids)} stray CS2 process(es) on port {server.game_port}, terminating...",
                )

                # Kill the processes
                for pid in pids:
                    if pid:
                        kill_cmd = f"kill -9 {pid} 2>/dev/null || true"
                        await self.execute_command(kill_cmd, timeout=5)

                # Give a moment for processes to terminate
                await asyncio.sleep(0.5)

                # Verify they're gone using the same precise pattern
                verify_cmd = f"pgrep -f 'cs2.*-port\\s+{server.game_port}\\b' || true"
                success, verify_output, _ = await self.execute_command(verify_cmd, timeout=10)

                if verify_output.strip():
                    await self._send_progress_if_callback(
                        progress_callback, "⚠ Some CS2 processes may still be running"
                    )
                else:
                    await self._send_progress_if_callback(
                        progress_callback, "✓ All stray CS2 processes terminated"
                    )

        except Exception as e:
            # Non-critical error, log but continue
            await self._send_progress_if_callback(
                progress_callback, f"Note: Error checking for stray CS2 processes: {str(e)}"
            )

    async def _execute_steamcmd_with_retry(
        self,
        command: str,
        server: Server,
        progress_callback=None,
        timeout: int = 1800,
        max_retries: int | None = None,
        completion_check: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> Tuple[bool, str, str]:
        """
        Execute SteamCMD with retry and optional artifact verification.

        When ``completion_check`` is supplied, its result is authoritative. This
        handles both interrupted downloads with a zero exit code and completed
        installs where SteamCMD returns a non-zero status after self-updating.

        Args:
            command: SteamCMD command to execute
            server: Server instance
            progress_callback: Optional async callback for progress updates
            timeout: Command timeout in seconds
            max_retries: Recovery attempts after the first run. ``None`` loads
                the owner's personal-center setting (default 20).
            completion_check: Async check run after every exit/error. A false
                result forces a retry unless the failure is permanent (disk full).

        Returns:
            Tuple[bool, str, str]: (success, stdout, stderr)
        """
        if max_retries is None:
            max_retries = await _legacy_resolve_max_retries(getattr(server, "user_id", None))
        else:
            max_retries = clamp_steamcmd_max_retries(max_retries)

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        async def completion_is_verified() -> Optional[bool]:
            if completion_check is None:
                return None
            try:
                return bool(await completion_check())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "SteamCMD completion check failed for server %s: %s",
                    server.id,
                    exc,
                )
                await send_progress(f"⚠ Could not verify SteamCMD completion: {exc}")
                return False

        # Attempt counter (0 = initial attempt, 1+ = retries)
        for attempt in range(max_retries + 1):
            stdout = ""
            stderr = ""
            retry_reason = ""
            retryable = False

            server_id = getattr(server, "id", None)
            if server_id is not None and await _legacy_cancel_requested(server_id):
                await send_progress("✗ SteamCMD force-stop requested; leaving this server's lock")
                return False, stdout, STEAMCMD_FORCE_TERMINATED

            if not await self._prepare_steamcmd_retry(
                server, attempt, max_retries, send_progress, progress_callback
            ):
                return False, stdout, STEAMCMD_FORCE_TERMINATED
            success, stdout, stderr, retry_reason, retryable = await self._run_steamcmd_attempt(
                command, server, send_progress, timeout
            )

            (
                result,
                stdout,
                stderr,
                retry_reason,
                retryable,
            ) = await self._process_steamcmd_completion(
                attempt,
                max_retries,
                send_progress,
                success,
                stdout,
                stderr,
                retry_reason,
                retryable,
                await completion_is_verified(),
            )
            if result is True:
                return True, stdout, stderr

            error_snippet = retry_reason[:200] if retry_reason else "Unknown error"
            if attempt < max_retries and retryable:
                await send_progress(
                    f"⚠ SteamCMD exited unexpectedly "
                    f"({attempt + 1}/{max_retries + 1}); auto-recovering: {error_snippet}"
                )
                logger.warning(
                    "SteamCMD attempt %s failed for server %s: %s",
                    attempt + 1,
                    server.id,
                    error_snippet,
                )
                continue

            if attempt >= max_retries:
                await send_progress(f"✗ SteamCMD failed after {max_retries} retry attempts")
                logger.error(
                    "SteamCMD failed for server %s after %s retries",
                    server.id,
                    max_retries,
                )
            else:
                await send_progress("✗ SteamCMD failed with non-retryable error")
                logger.error(
                    "SteamCMD failed for server %s with non-retryable error",
                    server.id,
                )
            return False, stdout, stderr

        raise RuntimeError("SteamCMD retry loop ended unexpectedly")
