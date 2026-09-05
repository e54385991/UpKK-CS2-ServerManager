"""Game operations for SSHManager."""

# ruff: noqa: F403,F405

from modules.server_startup import (
    normalize_additional_parameters,
    normalize_default_map,
    resolved_game_mode,
)

from .common import *


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

    async def start_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:  # noqa: C901 - startup protocol.
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

        def sanitize_sensitive_value(cmd: str, value: str, replacement: str) -> str:
            """
            Helper to sanitize a sensitive value from a command string.
            Handles single quotes, double quotes, and unquoted occurrences.
            Processes quoted occurrences first, then unquoted to avoid partial exposure.
            Uses regex escaping to handle special characters safely.
            """
            if value is None or value.strip() == "":
                return cmd
            # Escape the value for safe string replacement (handles special characters)
            escaped_value = re.escape(value)
            # Replace quoted occurrences first (more specific matches)
            cmd = re.sub(f'"{escaped_value}"', f'"{replacement}"', cmd)
            cmd = re.sub(f"'{escaped_value}'", f"'{replacement}'", cmd)
            # Replace unquoted occurrences last (more general match)
            cmd = re.sub(escaped_value, replacement, cmd)
            return cmd

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
            stale_cleanup = cleanup_command("screen")
            if stale_cleanup:
                await self.execute_command(stale_cleanup, timeout=10)

            sessions_stopped, previous_managers = await self._stop_server_sessions_connected(
                server,
                progress_callback=progress_callback,
                retries=3,
            )
            if not sessions_stopped:
                return False, (
                    "Cannot start server because an existing screen/tmux "
                    "session could not be terminated safely"
                )
            if previous_managers:
                await send_progress("✓ Existing game session(s) terminated")

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
            cs2_executable = "./cs2"  # Use relative path when in correct directory

            # Get configuration with safe defaults
            try:
                default_map = normalize_default_map(server.default_map or "de_dust2")
                game_mode_str = server.game_mode or "competitive"
                game_type, game_mode = resolved_game_mode(game_mode_str, server.game_type)
                additional_parameters = normalize_additional_parameters(
                    server.additional_parameters
                )
            except ValueError as exc:
                message = f"Invalid startup configuration: {exc}"
                await send_progress(message)
                return False, message
            max_players = server.max_players or 32
            server_name = server.server_name or f"CS2 Server {server.id}"

            # Core parameters
            # Note: -tickrate is no longer supported in CS2
            params = [
                "-dedicated",
                f"-port {server.game_port}",
                f"+map {default_map}",
                f"-maxplayers {max_players}",
                f'+hostname "{server_name}"',
            ]

            # Optional IP binding
            if server.ip_address:
                params.append(f"-ip {server.ip_address}")

            # Client port (usually game_port + 1)
            if server.client_port:
                params.append(f"+clientport {server.client_port}")
            elif server.game_port:
                params.append(f"+clientport {server.game_port + 1}")

            # Steam account token (GSLT) - required only for public servers
            gslt_parameter = gslt_startup_parameter(server.steam_account_token)
            if gslt_parameter:
                params.append(gslt_parameter)

            # Server password
            if server.server_password:
                params.append(f'+sv_password "{server.server_password}"')

            # RCON password
            if server.rcon_password:
                params.append(f'+rcon_password "{server.rcon_password}"')

            # Game mode and type
            params.append(f"+game_mode {game_mode}")
            params.append(f"+game_type {game_type}")

            # SourceTV configuration
            if server.tv_enable and server.tv_port:
                params.extend(
                    [
                        "+tv_enable 1",
                        f"+tv_port {server.tv_port}",
                        '+tv_name "GOTV"',
                    ]
                )

            # Additional custom parameters
            if additional_parameters:
                params.append(additional_parameters)

            # Combine all parameters
            params_str = " ".join(params)

            # Get backend URL and API key for status reporting
            # Use server's backend_url if set, otherwise use global setting
            from modules.config import settings

            backend_url = server.backend_url or settings.BACKEND_URL
            api_key = server.api_key or ""

            # Check if autorestart script exists (should have been deployed during deployment)
            autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"
            check_script_cmd = f"test -f {autorestart_script_path} && echo 'exists'"
            script_exists_success, script_exists_stdout, _ = await self.execute_command(
                check_script_cmd
            )

            # If script doesn't exist, deploy it now
            if not script_exists_success or "exists" not in script_exists_stdout:
                await send_progress("Auto-restart script not found, deploying now...")

                # Read the autorestart script content
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                local_script_path = os.path.join(script_dir, "scripts", "cs2_autorestart.sh")

                try:
                    async with await anyio.open_file(local_script_path, "r") as script_file:
                        script_content = await script_file.read()

                    # Create the script on remote server
                    create_script_cmd = f"cat > {autorestart_script_path} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT"
                    success, stdout, stderr = await self.execute_command(
                        create_script_cmd, timeout=10
                    )

                    if not success:
                        await send_progress(
                            f"⚠ Warning: Could not deploy autorestart script: {stderr}"
                        )
                        await send_progress("Server will start without auto-restart protection")
                        use_autorestart = False
                    else:
                        # Make script executable
                        chmod_script_cmd = f"chmod +x {autorestart_script_path}"
                        await self.execute_command(chmod_script_cmd)
                        await send_progress("✓ Auto-restart wrapper script deployed")
                        use_autorestart = True
                except Exception as e:
                    await send_progress(f"⚠ Warning: Could not read autorestart script: {str(e)}")
                    await send_progress("Server will start without auto-restart protection")
                    use_autorestart = False
            else:
                await send_progress("✓ Auto-restart script found")
                use_autorestart = True

            # LGSM-style startup: Set working directory, library path, and redirect output
            # Working directory must be the bin directory for CS2 to find its libraries
            game_bin_dir = f"{server.game_directory}/cs2/game/bin/linuxsteamrt64"

            # Build the CS2 command independently of its session manager.
            cs2_start_cmd = (
                f"cd {shlex.quote(game_bin_dir)} && "
                f"export LD_LIBRARY_PATH={shlex.quote(game_bin_dir)}:"
                f'"${{LD_LIBRARY_PATH:-}}" && '
                f"{cs2_executable} {params_str}"
            )

            # CPU affinity belongs to the payload, not the tmux client.  A
            # long-lived tmux daemon would otherwise retain its old affinity.
            cpu_affinity = None
            if server.cpu_affinity:
                affinity = server.cpu_affinity.strip()
                if re.fullmatch(r"[\d,\-\s]+", affinity):
                    cpu_affinity = affinity
                    await send_progress(f"✓ CPU affinity configured: cores {affinity}")
                else:
                    await send_progress(
                        f"⚠ Warning: Invalid CPU affinity format '{server.cpu_affinity}', ignoring"
                    )

            if use_autorestart and api_key:
                payload = (
                    f"bash {shlex.quote(autorestart_script_path)} "
                    f"{server.id} {shlex.quote(api_key)} "
                    f"{shlex.quote(backend_url)} {shlex.quote(server.game_directory)} "
                    f"{shlex.quote(cs2_start_cmd)}"
                )
                start_cmd = start_session_command(
                    manager,
                    name,
                    payload,
                    cpu_affinity,
                )
                await send_progress("✓ Starting with auto-restart protection enabled")
            else:
                console_log = f"{server.game_directory}/cs2/game/csgo/console.log"
                shell_payload = (
                    f"cd {shlex.quote(game_bin_dir)} && "
                    f"export LD_LIBRARY_PATH={shlex.quote(game_bin_dir)}:"
                    f'"${{LD_LIBRARY_PATH:-}}" && '
                    f"{cs2_executable} {params_str} 2>&1 | "
                    f"tee {shlex.quote(console_log)}"
                )
                payload = f"bash -c {shlex.quote(shell_payload)}"
                start_cmd = start_session_command(
                    manager,
                    name,
                    payload,
                    cpu_affinity,
                )
                if not api_key:
                    await send_progress(
                        "⚠ Warning: No API key configured, auto-restart reporting disabled"
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
            sanitized_cmd = start_cmd
            sanitized_cmd = sanitize_sensitive_value(sanitized_cmd, api_key, "***API_KEY***")
            if server.server_password:
                sanitized_cmd = sanitize_sensitive_value(
                    sanitized_cmd, server.server_password, "***PASSWORD***"
                )
            if server.rcon_password:
                sanitized_cmd = sanitize_sensitive_value(
                    sanitized_cmd, server.rcon_password, "***RCON_PASSWORD***"
                )
            normalized_gslt = (server.steam_account_token or "").strip()
            sanitized_cmd = sanitize_sensitive_value(
                sanitized_cmd,
                normalized_gslt,
                "***STEAM_TOKEN***",
            )
            await send_progress(sanitized_cmd)
            await send_progress("=" * 60)

            success, stdout, stderr = await self.execute_command(start_cmd, timeout=10)

            if not success:
                await send_progress(f"Start command failed: {stderr}")
                return False, f"Start command failed: {stderr}"

            await send_progress("Server process started, streaming console output...")
            await send_progress("=" * 60)

            # Stream console output in real-time for first few seconds
            console_log_path = f"{server.game_directory}/cs2/game/csgo/console.log"

            # Wait a moment for log file to be created
            await asyncio.sleep(0.3)

            # Stream console output using tail -f with timeout
            # This will show the actual server startup messages (like srcds)
            stream_cmd = f"timeout 4 tail -f {console_log_path} 2>/dev/null || true"

            # Use execute_command_streaming to show real-time output
            try:
                await self.execute_command_streaming(
                    stream_cmd, output_callback=progress_callback, timeout=5
                )
            except Exception:
                # Timeout is expected - just continue
                pass

            await send_progress("=" * 60)
            await send_progress("Initial startup output complete, verifying server status...")
            await send_progress("=" * 60)

            # Early check: verify the configured session was created.
            # Wait a bit longer as initialization can take time
            await asyncio.sleep(0.8)
            running_managers = await self._running_server_session_managers(server)

            if manager not in running_managers:
                # The selected session never started or exited during initialization.
                log_check = f"test -f {server.game_directory}/cs2/game/csgo/console.log && tail -150 {server.game_directory}/cs2/game/csgo/console.log || echo 'No log file'"
                _, immediate_log, _ = await self.execute_command(log_check, timeout=10)

                # Check if auto-restart is available
                can_restart, restart_msg = server_monitor.can_restart(server.id)

                # Check for specific errors in the log
                error_analysis = []
                auto_restart_possible = True

                if immediate_log and immediate_log != "No log file":
                    if (
                        "map" in immediate_log.lower()
                        and "load" in immediate_log.lower()
                        and "fail" in immediate_log.lower()
                    ):
                        error_analysis.append(
                            "⚠ Map loading failed - the specified map may not exist or is corrupted"
                        )
                        auto_restart_possible = False  # Map issue won't be fixed by restart
                    if "error" in immediate_log.lower():
                        error_analysis.append("⚠ Server reported errors during initialization")
                    if "quit" in immediate_log.lower() or "exit" in immediate_log.lower():
                        error_analysis.append("⚠ Server exited during startup")
                    if (
                        "segmentation" in immediate_log.lower()
                        or "sigsegv" in immediate_log.lower()
                    ):
                        error_analysis.append("⚠ Server crashed (segmentation fault)")
                    if (
                        "bind" in immediate_log.lower()
                        or "address already in use" in immediate_log.lower()
                    ):
                        error_analysis.append("⚠ Port binding failed - port may already be in use")
                        auto_restart_possible = False  # Port issue won't be fixed by restart
                    if (
                        "steamclient.so" in immediate_log.lower()
                        and "fail" in immediate_log.lower()
                    ):
                        error_analysis.append(
                            "⚠ CRITICAL: steamclient.so loading failed - may need to re-deploy server"
                        )
                        auto_restart_possible = False

                # Attempt auto-restart if applicable
                if can_restart and auto_restart_possible and progress_callback:
                    await send_progress("\n" + "=" * 60)
                    await send_progress(
                        "AUTO-RESTART: Server crashed, attempting automatic restart..."
                    )
                    await send_progress(f"Restart status: {restart_msg}")
                    await send_progress("=" * 60)

                    server_monitor.record_restart(server.id)

                    # Log auto-restart to Redis for monitoring audit trail
                    # Local import to avoid circular dependency with services/__init__.py
                    try:
                        from services import redis_manager

                        await redis_manager.append_monitoring_log(
                            server_id=server.id,
                            event_type="auto_restart",
                            status="info",
                            message="Auto-restart triggered after immediate crash detection during start_server",
                        )
                    except Exception as e:
                        logger.error(f"Failed to log auto-restart to Redis: {e}")

                    # Wait a bit before restart
                    await asyncio.sleep(2)

                    # Retry starting the server (recursive call with same callback)
                    restart_success, restart_result_msg = await self.start_server(
                        server, progress_callback
                    )
                    server_monitor.queue_restart_notification(
                        server,
                        success=restart_success,
                        title=f"Immediate crash auto-restart {'completed' if restart_success else 'failed'}",
                        message=restart_result_msg,
                        trigger="immediate crash detection during start_server",
                        details={
                            "Detected Issues": "\n".join(error_analysis)
                            if error_analysis
                            else "Process exited during initialization",
                            "Restart Status": restart_msg,
                        },
                    )
                    return restart_success, restart_result_msg

                # If auto-restart not available or not applicable, return error
                error_msg = "Server failed to start - process exited during initialization.\n\n"
                if error_analysis:
                    error_msg += "Detected Issues:\n" + "\n".join(error_analysis) + "\n\n"
                if not can_restart:
                    error_msg += f"Auto-restart: {restart_msg}\n\n"
                elif not auto_restart_possible:
                    error_msg += "Auto-restart: Disabled due to configuration issues detected\n\n"
                error_msg += f"Console output (last 150 lines):\n{immediate_log[:3000]}"
                server_monitor.queue_restart_notification(
                    server,
                    success=False,
                    title="Immediate crash detected",
                    message=error_msg,
                    trigger="immediate crash detection during start_server",
                    details={
                        "Detected Issues": "\n".join(error_analysis)
                        if error_analysis
                        else "Process exited during initialization",
                        "Restart Status": restart_msg,
                    },
                )
                return False, error_msg

            # Wait 1 second and check if server is still alive (detect immediate crashes)
            await asyncio.sleep(1)
            running_managers = await self._running_server_session_managers(server)

            if manager not in running_managers:
                # Server crashed within 1 second - get logs immediately
                log_check = f"test -f {server.game_directory}/cs2/game/csgo/console.log && tail -100 {server.game_directory}/cs2/game/csgo/console.log || echo 'No log file'"
                _, crash_log, _ = await self.execute_command(log_check, timeout=10)

                # Check for core dumps
                core_check = f"ls -lt {server.game_directory}/cs2/game/bin/linuxsteamrt64/core* 2>/dev/null | head -1 || echo 'No core dump'"
                _, core_output, _ = await self.execute_command(core_check)

                crash_info = "Server crashed within 1 second of starting.\n\n"
                crash_info += f"=== Console Log (last 100 lines) ===\n{crash_log[:3000]}\n\n"
                if "No core dump" not in core_output:
                    crash_info += f"=== Core Dump Found ===\n{core_output}\n"
                server_monitor.queue_restart_notification(
                    server,
                    success=False,
                    title="Immediate crash detected",
                    message=crash_info,
                    trigger="post-start quick check",
                    details={
                        "Core Dump": "Detected"
                        if "No core dump" not in core_output
                        else "Not detected",
                    },
                )
                return False, crash_info

            # Wait additional time for server to fully initialize (CS2 can take time)
            await asyncio.sleep(3)

            # Check if server is running - try multiple methods
            # Method 1: check the configured detached session.
            running_managers = await self._running_server_session_managers(server)

            if manager in running_managers:
                # Server started successfully, refresh steam.inf version cache
                try:
                    from services.steam_inf_service import steam_inf_service

                    success, version = await steam_inf_service.refresh_version_cache(server)
                    if success and version:
                        await send_progress(f"✓ Server version: {version}")
                except Exception as e:
                    # Non-critical, just log
                    await send_progress(f"Note: Could not refresh version cache: {str(e)}")

                return True, "Server started successfully"

            # Method 2: Check if CS2 process is running
            process_check = (
                f"pgrep -f 'cs2.*-port {server.game_port}' && echo 'running' || echo 'not running'"
            )
            proc_success, proc_stdout, _ = await self.execute_command(process_check)

            if "running" in proc_stdout:
                # Server started successfully, refresh steam.inf version cache
                try:
                    from services.steam_inf_service import steam_inf_service

                    success, version = await steam_inf_service.refresh_version_cache(server)
                    if success and version:
                        await send_progress(f"✓ Server version: {version}")
                except Exception as e:
                    # Non-critical, just log
                    await send_progress(f"Note: Could not refresh version cache: {str(e)}")

                return True, "Server started successfully (process verified)"

            # Method 3: Check if port is listening
            port_check = f"netstat -tuln | grep ':{server.game_port} ' || ss -tuln | grep ':{server.game_port} ' || echo 'not listening'"
            port_success, port_stdout, _ = await self.execute_command(port_check)

            if "not listening" not in port_stdout and port_stdout.strip():
                # Server started successfully, refresh steam.inf version cache
                try:
                    from services.steam_inf_service import steam_inf_service

                    success, version = await steam_inf_service.refresh_version_cache(server)
                    if success and version:
                        await send_progress(f"✓ Server version: {version}")
                except Exception as e:
                    # Non-critical, just log
                    await send_progress(f"Note: Could not refresh version cache: {str(e)}")

                return True, "Server started successfully (port listening)"

            # If no check confirms the server is running, it likely failed to start
            # Gather comprehensive diagnostic information
            diagnostics = []

            # Check console log with more lines
            log_check = f"test -f {server.game_directory}/cs2/game/csgo/console.log && tail -100 {server.game_directory}/cs2/game/csgo/console.log || echo 'No log file found'"
            log_success, log_output, _ = await self.execute_command(log_check, timeout=10)

            # Check for core dumps (indicates crash)
            core_check = f"ls -lt {server.game_directory}/cs2/game/bin/linuxsteamrt64/core* 2>/dev/null | head -1 || echo 'No core dump'"
            _, core_output, _ = await self.execute_command(core_check)

            # Check for common errors in the log
            error_indicators = []
            if log_output and log_output != "No log file found":
                if "bind:" in log_output.lower() or "address already in use" in log_output.lower():
                    error_indicators.append("Port binding issue - port may be in use")
                if "permission denied" in log_output.lower():
                    error_indicators.append("Permission denied - check file permissions")
                if "map" in log_output.lower() and (
                    "not found" in log_output.lower() or "failed" in log_output.lower()
                ):
                    error_indicators.append("Map loading failed - check if map exists")
                if "library" in log_output.lower() or ".so" in log_output.lower():
                    error_indicators.append("Missing library dependency")
                if (
                    "segmentation fault" in log_output.lower()
                    or "sigsegv" in log_output.lower()
                    or "core dumped" in log_output.lower()
                ):
                    error_indicators.append("Segmentation fault - server crashed")
                if "failed to load" in log_output.lower():
                    error_indicators.append("Failed to load required resources")
                if "error" in log_output.lower():
                    # Count how many errors
                    error_count = log_output.lower().count("error")
                    if error_count > 0:
                        error_indicators.append(f"Found {error_count} error(s) in console log")

            diagnostics.append("=== Startup Diagnostics ===")
            diagnostics.append(
                f"{manager} session: "
                f"{'Found but process may have exited' if manager in running_managers else 'NOT FOUND'}"
            )
            diagnostics.append(
                f"Process running: {'NO' if 'not running' in proc_stdout else 'UNKNOWN'}"
            )
            diagnostics.append(
                f"Port {server.game_port} listening: {'NO' if 'not listening' in port_stdout or not port_stdout.strip() else 'UNKNOWN'}"
            )

            if "No core dump" not in core_output:
                diagnostics.append(f"Core dump: FOUND - {core_output.strip()[:200]}")

            if error_indicators:
                diagnostics.append("\n=== Detected Issues ===")
                for indicator in error_indicators:
                    diagnostics.append(f"⚠ {indicator}")

            # Check working directory and binary
            binary_check = f"test -f {server.game_directory}/cs2/game/bin/linuxsteamrt64/cs2 && echo 'exists' || echo 'missing'"
            binary_success, binary_stdout, _ = await self.execute_command(binary_check)
            if "missing" in binary_stdout:
                diagnostics.append("\n⚠ CS2 executable not found - deployment may have failed")

            # Check library dependencies
            lib_check = f"cd {server.game_directory}/cs2/game/bin/linuxsteamrt64 && ldd ./cs2 2>&1 | grep 'not found' || echo 'all libraries found'"
            lib_success, lib_stdout, _ = await self.execute_command(lib_check, timeout=10)
            if "not found" in lib_stdout:
                diagnostics.append("\n=== Missing Libraries ===")
                diagnostics.append(lib_stdout.strip())

            # Check if steamclient.so exists (required)
            steamclient_check = f"test -f {server.game_directory}/cs2/game/bin/linuxsteamrt64/steamclient.so && echo 'found' || echo 'MISSING steamclient.so'"
            _, steamclient_output, _ = await self.execute_command(steamclient_check)
            if "MISSING" in steamclient_output:
                diagnostics.append(
                    "\n⚠ CRITICAL: steamclient.so not found - SteamCMD installation may be incomplete"
                )

            diagnostics.append("\n=== Console Log (last 100 lines) ===")
            diagnostics.append(log_output[:3000] if log_output else "No log output available")

            # Add troubleshooting suggestions
            diagnostics.append("\n=== Troubleshooting Suggestions ===")
            if error_indicators:
                diagnostics.append("1. Check the detected issues above")
            diagnostics.append("2. Verify all files were installed: Check deployment logs")
            diagnostics.append(
                "3. Ensure ports are available: netstat -tuln | grep " + str(server.game_port)
            )
            diagnostics.append(
                "4. Check server permissions: ls -la "
                + server.game_directory
                + "/cs2/game/bin/linuxsteamrt64/cs2"
            )

            diagnostic_message = "\n".join(diagnostics)

            return False, f"Server failed to start after multiple checks.\n\n{diagnostic_message}"

        except Exception as e:
            return False, f"Start error: {str(e)}"
        finally:
            await self.disconnect()
