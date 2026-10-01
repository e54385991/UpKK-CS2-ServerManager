"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("services.ssh.game_start")


async def _refresh_start_version(server: Server, send_progress) -> None:
    try:
        from services.steam_inf_service import steam_inf_service

        success, version = await steam_inf_service.refresh_version_cache(server)
        if success and version:
            await send_progress(f"✓ Server version: {version}")
    except Exception as e:
        # Non-critical, just log
        await send_progress(f"Note: Could not refresh version cache: {str(e)}")


async def _verify_startup(
    self: SSHMixinBase, server: Server, manager: str, progress_callback, send_progress
) -> tuple[bool, str]:
    console_log_path = f"{server.game_directory}/cs2/game/csgo/console.log"

    # Wait a moment for log file to be created
    await host.asyncio.sleep(0.3)

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
    await host.asyncio.sleep(0.8)
    running_managers = await self._running_server_session_managers(server)

    if manager not in running_managers:
        # The selected session never started or exited during initialization.
        return await host._handle_initial_start_exit(self, server, progress_callback, send_progress)

    # Wait 1 second and check if server is still alive (detect immediate crashes)
    await host.asyncio.sleep(1)
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
        host.server_monitor.queue_restart_notification(
            server,
            success=False,
            title="Immediate crash detected",
            message=crash_info,
            trigger="post-start quick check",
            details={
                "Core Dump": "Detected" if "No core dump" not in core_output else "Not detected",
            },
        )
        return False, crash_info

    # Wait additional time for server to fully initialize (CS2 can take time)
    await host.asyncio.sleep(3)

    # Check if server is running - try multiple methods
    # Method 1: check the configured detached session.
    running_managers = await self._running_server_session_managers(server)

    if manager in running_managers:
        # Server started successfully, refresh steam.inf version cache
        await host._refresh_start_version(server, send_progress)

        return True, "Server started successfully"

    # Method 2: Check if CS2 process is running
    process_check = (
        f"pgrep -f 'cs2.*-port {server.game_port}' && echo 'running' || echo 'not running'"
    )
    proc_success, proc_stdout, _ = await self.execute_command(process_check)

    if "running" in proc_stdout:
        # Server started successfully, refresh steam.inf version cache
        await host._refresh_start_version(server, send_progress)

        return True, "Server started successfully (process verified)"

    # Method 3: Check if port is listening
    port_check = f"netstat -tuln | grep ':{server.game_port} ' || ss -tuln | grep ':{server.game_port} ' || echo 'not listening'"
    port_success, port_stdout, _ = await self.execute_command(port_check)

    if "not listening" not in port_stdout and port_stdout.strip():
        # Server started successfully, refresh steam.inf version cache
        await host._refresh_start_version(server, send_progress)

        return True, "Server started successfully (port listening)"

    # If no check confirms the server is running, it likely failed to start
    # Gather comprehensive diagnostic information
    return await host._failed_start_diagnostics(
        self, server, manager, running_managers, proc_stdout, port_stdout
    )


async def _stop_existing_start_sessions(
    self: SSHMixinBase, server: Server, progress_callback, send_progress
) -> tuple[bool, str] | None:
    stale_cleanup = host.cleanup_command("screen")
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
    return None
