"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("services.ssh.game_start")


def _initial_start_log_issues(immediate_log: str) -> tuple[list[str], bool]:
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
        if "segmentation" in immediate_log.lower() or "sigsegv" in immediate_log.lower():
            error_analysis.append("⚠ Server crashed (segmentation fault)")
        if "bind" in immediate_log.lower() or "address already in use" in immediate_log.lower():
            error_analysis.append("⚠ Port binding failed - port may already be in use")
            auto_restart_possible = False  # Port issue won't be fixed by restart
        if "steamclient.so" in immediate_log.lower() and "fail" in immediate_log.lower():
            error_analysis.append(
                "⚠ CRITICAL: steamclient.so loading failed - may need to re-deploy server"
            )
            auto_restart_possible = False
    return error_analysis, auto_restart_possible


async def _handle_initial_start_exit(
    self: SSHMixinBase, server: Server, progress_callback, send_progress
) -> tuple[bool, str]:
    log_check = f"test -f {server.game_directory}/cs2/game/csgo/console.log && tail -150 {server.game_directory}/cs2/game/csgo/console.log || echo 'No log file'"
    _, immediate_log, _ = await self.execute_command(log_check, timeout=10)

    # Check if auto-restart is available
    protection_window = host.restart_protection_window(server)
    can_restart, restart_msg = host.server_monitor.can_restart(
        server.id,
        window=protection_window,
    )

    # Check for specific errors in the log
    error_analysis, auto_restart_possible = host._initial_start_log_issues(immediate_log)

    # Attempt auto-restart if applicable
    if can_restart and auto_restart_possible and progress_callback:
        await send_progress("\n" + "=" * 60)
        await send_progress("AUTO-RESTART: Server crashed, attempting automatic restart...")
        await send_progress(f"Restart status: {restart_msg}")
        await send_progress("=" * 60)

        host.server_monitor.record_restart(server.id, window=protection_window)

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
            host.logger.error(f"Failed to log auto-restart to Redis: {e}")

        # Wait a bit before restart
        await host.asyncio.sleep(2)

        # Retry starting the server (recursive call with same callback)
        restart_success, restart_result_msg = await self.start_server(server, progress_callback)
        host.server_monitor.queue_restart_notification(
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
    host.server_monitor.queue_restart_notification(
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


def _failed_start_log_issues(log_output: str) -> list[str]:
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
    return error_indicators


async def _failed_start_diagnostics(
    self: SSHMixinBase,
    server: Server,
    manager: str,
    running_managers: list[str],
    proc_stdout: str,
    port_stdout: str,
) -> tuple[bool, str]:
    diagnostics = []

    # Check console log with more lines
    log_check = f"test -f {server.game_directory}/cs2/game/csgo/console.log && tail -100 {server.game_directory}/cs2/game/csgo/console.log || echo 'No log file found'"
    log_success, log_output, _ = await self.execute_command(log_check, timeout=10)

    # Check for core dumps (indicates crash)
    core_check = f"ls -lt {server.game_directory}/cs2/game/bin/linuxsteamrt64/core* 2>/dev/null | head -1 || echo 'No core dump'"
    _, core_output, _ = await self.execute_command(core_check)

    # Check for common errors in the log
    error_indicators = host._failed_start_log_issues(log_output)

    diagnostics.append("=== Startup Diagnostics ===")
    diagnostics.append(
        f"{manager} session: "
        f"{'Found but process may have exited' if manager in running_managers else 'NOT FOUND'}"
    )
    diagnostics.append(f"Process running: {'NO' if 'not running' in proc_stdout else 'UNKNOWN'}")
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
