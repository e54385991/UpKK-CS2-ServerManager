"""Game operations for SSHManager."""

# ruff: noqa: F403,F405

from services.host_initialization import SshManagerHostRunner as SshManagerHostRunner
from services.host_initialization import ensure_steamcmd_packages as ensure_steamcmd_packages
from services.steamcmd_guard import (
    cs2_deploy_steamcmd_failure_message as cs2_deploy_steamcmd_failure_message,
)
from services.steamcmd_retry import resolve_steamcmd_max_retries as resolve_steamcmd_max_retries
from services.system_dependencies import APT_RETRY_ATTEMPTS as APT_RETRY_ATTEMPTS
from services.system_dependencies import APT_RETRY_DELAYS_SECONDS as APT_RETRY_DELAYS_SECONDS
from services.system_dependencies import STEAMCMD_REQUIRED_PACKAGES as STEAMCMD_REQUIRED_PACKAGES
from services.system_dependencies import apt_get_command as apt_get_command

from .autorestart_script import ensure_autorestart_script as ensure_autorestart_script
from .common import *
from .common import _cleanup_local_download_dir as _cleanup_local_download_dir
from .game_deployment_bootstrap import _bootstrap_steamcmd as _bootstrap_steamcmd
from .game_deployment_bootstrap import _download_steamcmd_panel as _download_steamcmd_panel
from .game_deployment_bootstrap import _download_steamcmd_remote as _download_steamcmd_remote
from .game_deployment_environment import _install_deployment_tools as _install_deployment_tools
from .game_deployment_environment import _prepare_cs2_home as _prepare_cs2_home
from .game_deployment_environment import _prepare_deployment_tools as _prepare_deployment_tools
from .game_deployment_finalize import _deployment_progress as _deployment_progress
from .game_deployment_finalize import _finish_cs2_deployment as _finish_cs2_deployment
from .game_deployment_finalize import _verify_deployed_executable as _verify_deployed_executable


class GameDeploymentMixin(SSHMixinBase):
    """Focused game lifecycle capability."""

    async def _steamcmd_host_preflight_connected(self, progress_callback=None) -> Tuple[bool, str]:
        """Detect missing SteamCMD packages, install them when possible, then re-verify."""
        server = getattr(self, "current_server", None)
        if server is None:
            return False, "Not connected to a server host"

        async def send_progress(message: str):
            if progress_callback is None:
                return
            if inspect.iscoroutinefunction(progress_callback):
                await progress_callback(message)
            else:
                progress_callback(message)

        result = await ensure_steamcmd_packages(
            SshManagerHostRunner(self, server),
            STEAMCMD_REQUIRED_PACKAGES,
            progress=send_progress,
            preferred_mirror=getattr(server, "apt_mirror", None),
            apply_preferred_first=bool(getattr(server, "apt_mirror", None)),
        )
        if result.apt_mirror and getattr(server, "apt_mirror", None) != result.apt_mirror:
            server.apt_mirror = result.apt_mirror
        return result.success, result.message

    async def deploy_cs2_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Deploy CS2 server on Ubuntu 24.04+ without requiring sudo
        Similar to LinuxGSM approach - works entirely in user space

        Prerequisites (must be installed by system administrator):
        - lib32gcc-s1, lib32stdc++6, curl, wget, tar, screen or tmux, unzip

        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """

        async def send_progress(message: str):
            await _deployment_progress(progress_callback, message)

        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

        try:
            await send_progress("Checking SteamCMD architecture and 32-bit runtime...")
            preflight_success, preflight_message = await self._steamcmd_host_preflight_connected(
                send_progress
            )
            if not preflight_success:
                await send_progress(f"✗ {preflight_message}")
                return False, preflight_message
            await send_progress(f"✓ {preflight_message}")

            # Check if environment is initialized (cs2server user exists)
            await send_progress("Checking environment initialization...")

            # Check if game_directory path suggests we need cs2server user
            if "/home/cs2server" in server.game_directory:
                # Check if cs2server user exists
                home_failure = await _prepare_cs2_home(self, server, send_progress)
                if home_failure is not None:
                    return home_failure

            # Check if required tools are available
            await send_progress("Checking system prerequisites...")
            tools_failure = await _prepare_deployment_tools(self, server, send_progress)
            if tools_failure is not None:
                return tools_failure

            # Create directory
            await send_progress(f"Creating game directory: {server.game_directory}")
            success, stdout, stderr = await self.execute_command(
                f"mkdir -p {server.game_directory}"
            )
            if not success:
                return False, f"Directory creation failed: {stderr}"
            await send_progress("✓ Game directory created successfully")

            # Download and install SteamCMD
            bootstrap_success, steamcmd_dir = await _bootstrap_steamcmd(self, server, send_progress)
            if not bootstrap_success:
                return False, steamcmd_dir

            # Install CS2 server (App ID: 730) with streaming output and automatic retry
            max_retries = await resolve_steamcmd_max_retries(getattr(server, "user_id", None))
            await send_progress("=" * 60)
            await send_progress("Installing CS2 server via SteamCMD...")
            await send_progress("This will download approximately 30GB and may take 15-30 minutes")
            await send_progress(
                f"Auto-retry is enabled: up to {max_retries} recoveries after "
                "network drops, crashes, or an unexpected SteamCMD exit"
            )
            await send_progress(
                "SteamCMD runs in a detached tmux/screen session so SSH "
                "reconnect does not stop the download."
            )
            await send_progress("Please be patient, you will see real-time progress below:")
            await send_progress("=" * 60)

            install_cs2 = (
                f"cd {steamcmd_dir} && "
                f"./steamcmd.sh "
                f"+force_install_dir {server.game_directory}/cs2 "
                f"+login anonymous "
                f"+app_update 730 validate "
                f"+quit"
            )

            # Display command preview before execution
            await send_progress("")
            await send_progress("即将执行的命令 / Commands to be executed:")
            await send_progress("=" * 60)
            await send_progress("📝 SteamCMD Install Command:")
            await send_progress(f"   {install_cs2}")
            await send_progress("=" * 60)
            await send_progress("")

            async def verify_cs2_installation() -> bool:
                return await _verify_deployed_executable(self, server, send_progress)

            # A SteamCMD zero exit code is not sufficient: interrupted downloads
            # can leave an incomplete tree. Verify the actual server executable
            # after every attempt and retry even for otherwise non-retryable exits.
            success, stdout, stderr = await self._execute_steamcmd_with_retry(
                install_cs2,
                server,
                progress_callback=send_progress,
                timeout=1800,  # 30 minutes per attempt
                max_retries=max_retries,
                completion_check=verify_cs2_installation,
            )

            if not success:
                executable_path = self._cs2_executable_path(server)
                error_detail = stderr.strip() if stderr else "Installation incomplete"
                alarm_message = cs2_deploy_steamcmd_failure_message(
                    max_retries=max_retries,
                    executable_path=executable_path,
                    error_detail=error_detail,
                )
                await send_progress(alarm_message)
                logger.error(
                    "CS2 deployment aborted for server %s: executable missing at %s "
                    "after %s retries",
                    server.id,
                    executable_path,
                    max_retries,
                )
                return False, alarm_message

            # Fix steamclient.so symlink issue (required for CS2 to start)
            # See: https://developer.valvesoftware.com/wiki/Counter-Strike_2/Dedicated_Servers#Troubleshooting
            await send_progress("=" * 60)
            await send_progress("Fixing steamclient.so symlink (required for server startup)...")
            await send_progress("=" * 60)

            # Create ~/.steam/sdk64 directory if it doesn't exist
            return await _finish_cs2_deployment(self, server, steamcmd_dir, send_progress)

        except Exception as e:
            await send_progress(f"Deployment error: {str(e)}")
            return False, f"Deployment error: {str(e)}"
        finally:
            await self.disconnect()
