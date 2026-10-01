"""Game operations for SSHManager."""

# ruff: noqa: F403,F405

from modules.server_startup import (
    normalize_additional_parameters as normalize_additional_parameters,
)
from modules.server_startup import normalize_default_map as normalize_default_map
from modules.server_startup import resolved_game_mode as resolved_game_mode
from services.restart_protection import restart_protection_window as restart_protection_window

from .autorestart_script import ensure_autorestart_script as ensure_autorestart_script
from .common import *
from .game_start_commands import _build_start_command as _build_start_command
from .game_start_commands import _build_startup_parameters as _build_startup_parameters
from .game_start_commands import _prepare_start_wrapper as _prepare_start_wrapper
from .game_start_commands import _report_start_command as _report_start_command
from .game_start_commands import sanitize_sensitive_value as sanitize_sensitive_value
from .game_start_failures import _failed_start_diagnostics as _failed_start_diagnostics
from .game_start_failures import _failed_start_log_issues as _failed_start_log_issues
from .game_start_failures import _handle_initial_start_exit as _handle_initial_start_exit
from .game_start_failures import _initial_start_log_issues as _initial_start_log_issues
from .game_start_verification import _refresh_start_version as _refresh_start_version
from .game_start_verification import _stop_existing_start_sessions as _stop_existing_start_sessions
from .game_start_verification import _verify_startup as _verify_startup


class GameStartMixin(SSHMixinBase):
    """Focused game lifecycle capability."""

    async def _stop_server_sessions_connected(
        self,
        server: Server,
        progress_callback=None,
        retries: int = 3,
    ) -> Tuple[bool, List[str]]:
        """Stop every matching screen/tmux session without closing SSH.

        Checking both managers is intentional: a user may change the preferred
        manager while a legacy session is still running.  tmux force-stop is
        session-scoped by ``game_session`` and never kills its shared server.

        Returns ``(all_stopped, managers_found_before_stop)``.
        """
        name = session_name(server.id)
        initial_managers = await self._running_server_session_managers(server)
        if not initial_managers:
            return True, []

        await self._send_progress_if_callback(
            progress_callback,
            "Found existing game session(s): " + ", ".join(initial_managers),
        )

        remaining = initial_managers
        for attempt in range(max(1, retries)):
            for manager in remaining:
                await self.execute_command(
                    stop_session_command(manager, name),
                    timeout=10,
                )

            await asyncio.sleep(1)
            remaining = await self._running_server_session_managers(server)
            if not remaining:
                return True, initial_managers

            if attempt < max(1, retries) - 1:
                await self._send_progress_if_callback(
                    progress_callback,
                    f"Waiting for {', '.join(remaining)} session(s) to terminate...",
                )

        await self._send_progress_if_callback(
            progress_callback,
            "Session shutdown timed out; applying manager-scoped force stop...",
        )
        for manager in remaining:
            await self.execute_command(
                force_stop_session_command(manager, name),
                timeout=10,
            )

        await asyncio.sleep(1)
        remaining = await self._running_server_session_managers(server)
        return not remaining, initial_managers

    async def start_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Start CS2 server with LGSM-style configuration and real-time output streaming

        This method includes defensive checks to ensure no duplicate screen or
        tmux sessions.  It also cleans up a legacy session when the configured
        manager has changed.
        """
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                await progress_callback(message)

        try:
            executable_exists, executable_path = await self._cs2_executable_exists_connected(server)
            if not executable_exists:
                message = self._missing_cs2_executable_message(executable_path)
                await send_progress(f"✗ {message}")
                return False, message

            manager = normalize_session_manager(server.session_manager)
            name = session_name(server.id)
            await send_progress(f"Using {manager} as the game session manager")

            (
                manager_available,
                manager_message,
            ) = await self._configured_session_manager_available_connected(server)
            if not manager_available:
                return False, (
                    f"Cannot start server: {manager_message}. "
                    "Please install it on the remote host first."
                )

            # GNU screen can leave dead sockets. tmux removes dead sessions
            # itself, so it deliberately has no equivalent global cleanup.
            session_failure = await _stop_existing_start_sessions(
                self, server, progress_callback, send_progress
            )
            if session_failure is not None:
                return session_failure

            # Kill any stray CS2 processes which no longer have a live session.
            # This is an additional safety check to prevent duplicate processes
            await self._kill_stray_cs2_processes(server, progress_callback)

            # Perform universal self-check and auto-fix common issues
            selfcheck_success, selfcheck_msg = await self.perform_server_selfcheck(
                server, progress_callback
            )
            if not selfcheck_success:
                await send_progress(f"⚠ Warning: Self-check found issues: {selfcheck_msg}")
                await send_progress("Continuing with server start...")

            # Build start command with LGSM-style parameters
            try:
                params_str, default_map, game_mode_str, game_type, game_mode, max_players = (
                    _build_startup_parameters(server)
                )
            except ValueError as exc:
                message = f"Invalid startup configuration: {exc}"
                await send_progress(message)
                return False, message
            cs2_executable = "./cs2"

            # Get backend URL and API key for status reporting
            # Use server's backend_url if set, otherwise use global setting
            from modules.config import settings

            backend_url = server.backend_url or settings.BACKEND_URL
            api_key = server.api_key or ""

            # Refresh the wrapper when the host copy is missing or older than the repo.
            use_autorestart, autorestart_script_path = await _prepare_start_wrapper(
                self, server, send_progress
            )

            # LGSM-style startup: Set working directory, library path, and redirect output
            # Working directory must be the bin directory for CS2 to find its libraries
            start_cmd = await _build_start_command(
                server,
                manager,
                name,
                params_str,
                cs2_executable,
                use_autorestart,
                api_key,
                backend_url,
                autorestart_script_path,
                send_progress,
            )

            # Send startup information
            await send_progress("=" * 60)
            await send_progress("Starting CS2 Server...")
            await send_progress("=" * 60)
            await send_progress(f"Server ID: {server.id}")
            await send_progress(f"Port: {server.game_port}")
            await send_progress(f"Map: {default_map}")
            await send_progress(f"Max Players: {max_players}")
            await send_progress(
                f"Game Mode: {game_mode_str} (game_type: {game_type}, game_mode: {game_mode})"
            )
            await send_progress("=" * 60)
            await send_progress("Startup Command:")
            # Sanitize sensitive information before displaying
            await _report_start_command(server, start_cmd, api_key, send_progress)
            await send_progress("=" * 60)

            success, stdout, stderr = await self.execute_command(start_cmd, timeout=10)

            if not success:
                await send_progress(f"Start command failed: {stderr}")
                return False, f"Start command failed: {stderr}"

            await send_progress("Server process started, streaming console output...")
            await send_progress("=" * 60)

            # Stream console output in real-time for first few seconds
            return await _verify_startup(self, server, manager, progress_callback, send_progress)

        except Exception as e:
            return False, f"Start error: {str(e)}"
        finally:
            await self.disconnect()
