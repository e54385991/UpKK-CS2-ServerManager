"""Game operations for SSHManager."""

# ruff: noqa: F403,F405

from services.steamcmd_retry import (
    resolve_steamcmd_max_retries,
)

from .common import *


class GameUpdateMixin(SSHMixinBase):
    """Focused game lifecycle capability."""

    async def _prepare_update_session(self, server: Server, send_progress, progress_callback):
        await self._kill_steamcmd_processes(server, progress_callback)
        running_managers = await self._running_server_session_managers(server)
        if not running_managers:
            return False, None
        (
            manager_available,
            manager_message,
        ) = await self._configured_session_manager_available_connected(server)
        if not manager_available:
            return True, (
                f"Server update aborted before stopping: {manager_message}. "
                "The existing game session was left running."
            )
        await send_progress(
            f"Server is running in {', '.join(running_managers)}, stopping before update..."
        )
        all_stopped, _ = await self._stop_server_sessions_connected(
            server, progress_callback=progress_callback, retries=3
        )
        if not all_stopped:
            return (
                True,
                "Server update aborted because the existing screen/tmux session could not be stopped",
            )
        await send_progress("✓ Server stopped successfully")
        return True, None

    async def _restore_updated_server(
        self, server: Server, was_running: bool, progress_callback, send_progress
    ):
        if not was_running:
            return True, "Server updated successfully; server remained stopped"
        await send_progress("Restarting server...")
        restart_success, restart_msg = await self.start_server(server, progress_callback)
        if not restart_success:
            await send_progress(f"✗ Failed to restart server after update: {restart_msg}")
            return (
                False,
                f"Server files updated, but failed to restore the running server: {restart_msg}",
            )
        await send_progress("✓ Server restarted successfully after update")
        return True, "Server updated and restored to running state successfully"

    async def _clear_execstack_before_update(
        self, server: Server, send_progress, enabled: bool, targets=None
    ) -> None:
        """Clear legacy plugin ELF flags while the game is known to be stopped."""
        if not enabled:
            return
        from services.server_compatibility import execute_clear_execstack_on_manager

        await send_progress(
            "Clearing executable-stack flags from configured plugin targets before update..."
        )
        fixed, detail = await execute_clear_execstack_on_manager(self, server, targets)
        if fixed:
            await send_progress(f"✓ Plugin execstack cleanup completed: {detail}")
        else:
            await send_progress(f"⚠ Plugin execstack cleanup failed; continuing update: {detail}")

    async def update_server(
        self,
        server: Server,
        progress_callback=None,
        *,
        clear_execstack: bool = False,
        clear_execstack_targets=None,
    ) -> Tuple[bool, str]:
        """Update CS2 server using SteamCMD (without validation)"""
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        try:
            await send_progress("Starting server update...")

            was_running, preparation_error = await self._prepare_update_session(
                server, send_progress, progress_callback
            )
            if preparation_error:
                return False, preparation_error

            await self._clear_execstack_before_update(
                server, send_progress, clear_execstack, clear_execstack_targets
            )

            # Kill any stray CS2 processes left outside the managed session.
            await self._kill_stray_cs2_processes(server, progress_callback)

            # Navigate to game directory
            game_dir = server.game_directory
            steamcmd_dir = f"{game_dir}/steamcmd"

            # Run SteamCMD update command (without validate) with automatic retry
            update_cmd = (
                f"cd {steamcmd_dir} && "
                f"./steamcmd.sh "
                f"+force_install_dir {game_dir}/cs2 "
                f"+login anonymous "
                f"+app_update 730 "
                f"+quit"
            )

            # Display command preview before execution
            await send_progress("=" * 60)
            await send_progress("即将执行的命令 / Commands to be executed:")
            await send_progress("=" * 60)
            await send_progress("📝 SteamCMD Update Command:")
            await send_progress(f"   {update_cmd}")
            await send_progress("=" * 60)
            await send_progress("Updating CS2 server files via SteamCMD...")
            max_retries = await resolve_steamcmd_max_retries(getattr(server, "user_id", None))
            await send_progress(
                f"Auto-retry is enabled: up to {max_retries} "
                "automatic recoveries on network errors, crashes, or unexpected exits"
            )

            # Use retry mechanism for SteamCMD update
            success, stdout, stderr = await self._execute_steamcmd_with_retry(
                update_cmd,
                server,
                progress_callback=send_progress,
                timeout=1800,  # 30 minutes per attempt
                max_retries=max_retries,
            )

            if not success:
                # SteamCMD's launcher writes benign startup diagnostics to
                # stderr. Preserve both streams so that line doesn't hide a
                # useful success/error message from stdout.
                error_parts = []
                if stderr and stderr.strip():
                    error_parts.append(f"stderr: {stderr.strip()[-1000:]}")
                if stdout and stdout.strip():
                    error_parts.append(f"stdout: {stdout.strip()[-1000:]}")
                error_detail = "; ".join(error_parts) or "SteamCMD returned a failure status"
                await send_progress(f"CS2 server update failed: {error_detail}")
                recovery_detail = ""
                if was_running:
                    await send_progress("Attempting to restore the previously running server...")
                    recovery_success, recovery_message = await self.start_server(
                        server, progress_callback
                    )
                    recovery_detail = f"; recovery start {'succeeded' if recovery_success else 'failed'}: {recovery_message}"
                return False, f"SteamCMD update failed: {error_detail}{recovery_detail}"

            await send_progress("CS2 server updated successfully")

            # Refresh steam.inf version cache after update
            try:
                await send_progress("Refreshing version cache...")
                from services.steam_inf_service import steam_inf_service

                success, version = await steam_inf_service.refresh_version_cache(server)
                if success and version:
                    await send_progress(f"✓ Updated to version: {version}")
            except Exception as e:
                # Non-critical, just log
                await send_progress(f"Note: Could not refresh version cache: {str(e)}")

            return await self._restore_updated_server(
                server, was_running, progress_callback, send_progress
            )

        except Exception as e:
            await send_progress(f"Update error: {str(e)}")
            return False, f"Update error: {str(e)}"
        finally:
            await self.disconnect()

    async def validate_server(
        self,
        server: Server,
        progress_callback=None,
        *,
        clear_execstack: bool = False,
        clear_execstack_targets=None,
    ) -> Tuple[bool, str]:
        """Update and validate CS2 server files using SteamCMD"""
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        try:
            await send_progress("Starting server update and validation...")

            # Kill any existing steamcmd processes for this server
            await self._kill_steamcmd_processes(server, progress_callback)

            # Detect both the configured manager and a possible legacy session.
            running_managers = await self._running_server_session_managers(server)
            was_running = bool(running_managers)
            if was_running:
                (
                    manager_available,
                    manager_message,
                ) = await self._configured_session_manager_available_connected(server)
                if not manager_available:
                    return False, (
                        f"Server validation aborted before stopping: {manager_message}. "
                        "The existing game session was left running."
                    )
                await send_progress(
                    "Server is running in "
                    f"{', '.join(running_managers)}, stopping before validation..."
                )
                all_stopped, _ = await self._stop_server_sessions_connected(
                    server,
                    progress_callback=progress_callback,
                    retries=3,
                )
                if not all_stopped:
                    return False, (
                        "Server validation aborted because the existing "
                        "screen/tmux session could not be stopped"
                    )
                await send_progress("✓ Server stopped successfully")

            await self._clear_execstack_before_update(
                server, send_progress, clear_execstack, clear_execstack_targets
            )

            # Kill any stray CS2 processes left outside the managed session.
            await self._kill_stray_cs2_processes(server, progress_callback)

            # Navigate to game directory
            game_dir = server.game_directory
            steamcmd_dir = f"{game_dir}/steamcmd"

            # Run SteamCMD update command with validation and automatic retry
            update_cmd = (
                f"cd {steamcmd_dir} && "
                f"./steamcmd.sh "
                f"+force_install_dir {game_dir}/cs2 "
                f"+login anonymous "
                f"+app_update 730 validate "
                f"+quit"
            )

            # Display command preview before execution
            await send_progress("=" * 60)
            await send_progress("即将执行的命令 / Commands to be executed:")
            await send_progress("=" * 60)
            await send_progress("📝 SteamCMD Update + Validate Command:")
            await send_progress(f"   {update_cmd}")
            await send_progress("=" * 60)
            await send_progress("Updating and validating CS2 server files via SteamCMD...")
            await send_progress("This may take a while as all files will be validated...")
            max_retries = await resolve_steamcmd_max_retries(getattr(server, "user_id", None))
            await send_progress(
                f"Auto-retry is enabled: up to {max_retries} "
                "automatic recoveries on network errors, crashes, or unexpected exits"
            )

            # Use retry mechanism for SteamCMD validation
            success, stdout, stderr = await self._execute_steamcmd_with_retry(
                update_cmd,
                server,
                progress_callback=send_progress,
                timeout=10800,  # 3h per attempt
                max_retries=max_retries,
            )

            if not success and stderr and "error" in stderr.lower():
                await send_progress(f"Validation completed with warnings: {stderr}")
            else:
                await send_progress("CS2 server updated and validated successfully")

            # Refresh steam.inf version cache after validation
            try:
                await send_progress("Refreshing version cache...")
                from services.steam_inf_service import steam_inf_service

                success, version = await steam_inf_service.refresh_version_cache(server)
                if success and version:
                    await send_progress(f"✓ Validated version: {version}")
            except Exception as e:
                # Non-critical, just log
                await send_progress(f"Note: Could not refresh version cache: {str(e)}")

            # Restart server if it was running before
            if was_running:
                await send_progress("Restarting server...")
                # Actually restart the server instead of just suggesting it
                restart_success, restart_msg = await self.start_server(server, progress_callback)
                if restart_success:
                    await send_progress("✓ Server restarted successfully after validation")
                else:
                    await send_progress(
                        f"⚠ Warning: Failed to restart server after validation: {restart_msg}"
                    )
                    await send_progress("You may need to manually start the server")

            return True, "Server updated and validated successfully"

        except Exception as e:
            await send_progress(f"Validation error: {str(e)}")
            return False, f"Validation error: {str(e)}"
        finally:
            await self.disconnect()

    async def get_server_status(self, server: Server) -> Tuple[bool, str]:
        """Get server status"""
        success, msg = await self.connect(server)
        if not success:
            return False, "offline"

        try:
            running_managers = await self._running_server_session_managers(server)
            if running_managers:
                return True, "running"
            return True, "stopped"

        except Exception:
            return False, "unknown"
        finally:
            await self.disconnect()
