"""Game operations for SSHManager."""

# ruff: noqa: F403,F405

from modules.server_startup import (
    normalize_additional_parameters,
    normalize_default_map,
    resolved_game_mode,
)
from services.host_initialization import SshManagerHostRunner, ensure_steamcmd_packages
from services.steamcmd_guard import (
    STEAMCMD_FORCE_TERMINATED,
    cs2_deploy_steamcmd_failure_message,
    steamcmd_cancel_requested,
    steamcmd_pgrep_command,
)
from services.steamcmd_retry import (
    clamp_steamcmd_max_retries,
    is_steamcmd_failure_retryable,
    resolve_steamcmd_max_retries,
    steamcmd_retry_delay_seconds,
)
from services.steamcmd_session import (
    incremental_console_lines,
    latest_console_heartbeat,
    parse_steamcmd_exit_code,
    steamcmd_exit_path,
    wrap_steamcmd_payload,
)
from services.system_dependencies import (
    APT_RETRY_ATTEMPTS,
    APT_RETRY_DELAYS_SECONDS,
    STEAMCMD_REQUIRED_PACKAGES,
    apt_get_command,
)

from .common import *


class GameLifecycleMixin:
    """Internal game behavior; instantiate through SSHManager."""

    async def _steamcmd_host_preflight_connected(self, progress_callback=None) -> Tuple[bool, str]:
        """Detect missing SteamCMD packages, install them when possible, then re-verify."""
        server = getattr(self, "current_server", None)
        if server is None:
            return False, "Not connected to a server host"

        async def send_progress(message: str):
            if progress_callback is None:
                return
            if asyncio.iscoroutinefunction(progress_callback):
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
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

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
                check_user_cmd = "id cs2server > /dev/null 2>&1 && echo 'exists' || echo 'missing'"
                user_success, user_stdout, _ = await self.execute_command(check_user_cmd)

                if "missing" in user_stdout or not user_success:
                    await send_progress(
                        "✗ Environment not initialized: cs2server user does not exist"
                    )
                    return False, (
                        "Environment not initialized. Please create cs2server user first:\n"
                        "sudo useradd -m -s /bin/bash cs2server\n"
                        "sudo passwd cs2server\n"
                        "sudo usermod -aG sudo cs2server  # Optional: for installing dependencies\n\n"
                        "Or use a different game_directory path that the current user can access."
                    )

                await send_progress("✓ cs2server user exists")

                # Verify cs2server home directory has correct permissions
                check_perms_cmd = (
                    "test -w /home/cs2server && echo 'writable' || echo 'not_writable'"
                )
                perm_success, perm_stdout, _ = await self.execute_command(check_perms_cmd)

                if "not_writable" in perm_stdout or not perm_success:
                    await send_progress("✗ /home/cs2server directory is not writable")

                    # Try to fix permissions if we have sudo password
                    privileged_password = server.sudo_password or server.ssh_password
                    if privileged_password:
                        await send_progress("Attempting to fix permissions...")
                        fix_perms_cmd = f"echo '{privileged_password}' | sudo -S chown -R cs2server:cs2server /home/cs2server && echo '{privileged_password}' | sudo -S chmod 755 /home/cs2server"
                        fix_success, _, fix_stderr = await self.execute_command(fix_perms_cmd)

                        if fix_success:
                            await send_progress("✓ Permissions fixed for /home/cs2server")
                        else:
                            return False, (
                                "Cannot create directory in /home/cs2server: Permission denied.\n"
                                "Please ensure the directory has correct permissions:\n"
                                "sudo chown -R cs2server:cs2server /home/cs2server\n"
                                "sudo chmod 755 /home/cs2server"
                            )
                    else:
                        return False, (
                            "Cannot create directory in /home/cs2server: Permission denied.\n"
                            "Please ensure the directory has correct permissions:\n"
                            "sudo chown -R cs2server:cs2server /home/cs2server\n"
                            "sudo chmod 755 /home/cs2server"
                        )
                else:
                    await send_progress("✓ /home/cs2server is writable")

            # Check if required tools are available
            await send_progress("Checking system prerequisites...")
            session_manager = normalize_session_manager(server.session_manager)
            required_tools = ["wget", "tar", session_manager, "unzip"]
            missing_tools = []
            for tool in required_tools:
                success, stdout, stderr = await self.execute_command(f"command -v {tool}")
                if not success:
                    await send_progress(f"⚠ Warning: {tool} not found")
                    missing_tools.append(tool)
                else:
                    await send_progress(f"✓ Found {tool}: {stdout.strip()}")

            # Try to install missing tools
            if missing_tools:
                await send_progress(
                    f"Attempting to install missing tools: {', '.join(missing_tools)}"
                )
                # Check package manager
                check_apt = "command -v apt-get > /dev/null && echo 'apt' || echo 'none'"
                _, pkg_mgr, _ = await self.execute_command(check_apt)

                if "apt" in pkg_mgr:
                    install_cmd = (
                        f"{apt_get_command('update')} && "
                        f"{apt_get_command('install', missing_tools)}"
                    )
                    success, stdout, stderr = await self.execute_command(install_cmd, timeout=600)

                    if not success:
                        await send_progress("Trying to install with sudo and automatic retries...")
                        for attempt in range(1, APT_RETRY_ATTEMPTS + 1):
                            success, stdout, stderr = await self.execute_sudo_command(
                                install_cmd,
                                server.sudo_password or server.ssh_password,
                                timeout=600,
                            )
                            if success:
                                break
                            await send_progress(
                                f"⚠ Dependency installation attempt {attempt}/"
                                f"{APT_RETRY_ATTEMPTS} failed: {stderr.strip() or stdout.strip()}"
                            )
                            if attempt < APT_RETRY_ATTEMPTS:
                                delay = APT_RETRY_DELAYS_SECONDS[
                                    min(attempt - 1, len(APT_RETRY_DELAYS_SECONDS) - 1)
                                ]
                                await send_progress(f"Retrying in {delay} seconds...")
                                await asyncio.sleep(delay)

                        if success:
                            await send_progress(
                                f"✓ Successfully installed: {', '.join(missing_tools)}"
                            )
                        else:
                            await send_progress(
                                f"⚠ Could not install tools. Please run: "
                                f"sudo apt-get install {' '.join(missing_tools)}"
                            )
                    else:
                        await send_progress(f"✓ Successfully installed: {', '.join(missing_tools)}")

                unresolved_tools = []
                for tool in missing_tools:
                    tool_success, _, _ = await self.execute_command(f"command -v {tool}")
                    if not tool_success:
                        unresolved_tools.append(tool)
                if unresolved_tools:
                    return False, (
                        "Required tools are still missing after automatic installation: "
                        f"{', '.join(unresolved_tools)}"
                    )

            # Create directory
            await send_progress(f"Creating game directory: {server.game_directory}")
            success, stdout, stderr = await self.execute_command(
                f"mkdir -p {server.game_directory}"
            )
            if not success:
                return False, f"Directory creation failed: {stderr}"
            await send_progress("✓ Game directory created successfully")

            # Download and install SteamCMD
            steamcmd_dir = f"{server.game_directory}/steamcmd"
            await send_progress("Setting up SteamCMD...")

            # Create SteamCMD directory
            await send_progress("Creating SteamCMD directory...")
            success, stdout, stderr = await self.execute_command(f"mkdir -p {steamcmd_dir}")
            if not success:
                return False, f"SteamCMD directory creation failed: {stderr}"
            await send_progress("✓ SteamCMD directory created")

            # Download SteamCMD with streaming output
            await send_progress("Downloading SteamCMD...")

            # Check if panel proxy mode is enabled
            if server.use_panel_proxy:
                # Panel Proxy Mode: Download to panel server first, then upload via SFTP
                await send_progress("Using panel server proxy mode for SteamCMD download...")

                steamcmd_url = (
                    "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
                )
                steamcmd_local_path = None

                try:
                    # Create temp directory on panel server
                    panel_temp_dir = os.path.join(
                        tempfile.gettempdir(), f"cs2_panel_proxy_steamcmd_{server.user_id}"
                    )
                    os.makedirs(panel_temp_dir, exist_ok=True)

                    # Create unique subdirectory
                    download_id = str(uuid.uuid4())
                    download_dir = os.path.join(panel_temp_dir, download_id)
                    os.makedirs(download_dir, exist_ok=True)

                    steamcmd_local_path = os.path.join(download_dir, "steamcmd_linux.tar.gz")

                    # Download to panel server
                    from modules.http_helper import http_helper

                    last_progress = 0

                    async def download_progress_callback(bytes_downloaded, total_bytes):
                        nonlocal last_progress
                        if total_bytes > 0:
                            percent = int((bytes_downloaded / total_bytes) * 100)
                            if percent >= last_progress + 10 or percent == 100:
                                last_progress = percent
                                size_mb = bytes_downloaded / (1024 * 1024)
                                total_mb = total_bytes / (1024 * 1024)
                                await send_progress(
                                    f"Download progress: {percent}% ({size_mb:.1f}/{total_mb:.1f} MB)"
                                )

                    success_download, error = await http_helper.download_file(
                        steamcmd_url,
                        steamcmd_local_path,
                        timeout=600,
                        progress_callback=download_progress_callback,
                    )

                    if not success_download:
                        raise Exception(f"Failed to download SteamCMD: {error}")

                    # Verify file size
                    file_size = os.path.getsize(steamcmd_local_path)
                    if file_size < 1000:
                        raise Exception("Downloaded SteamCMD file is too small or empty")

                    await send_progress(
                        f"Download complete ({file_size / (1024 * 1024):.2f} MB), uploading to server..."
                    )

                    # Upload to remote server via SFTP
                    remote_steamcmd_path = f"{steamcmd_dir}/steamcmd_linux.tar.gz"

                    last_upload = 0

                    async def upload_progress_callback(bytes_uploaded, total_bytes):
                        nonlocal last_upload
                        if total_bytes > 0:
                            percent = int((bytes_uploaded / total_bytes) * 100)
                            if percent >= last_upload + 10 or percent == 100:
                                last_upload = percent
                                size_mb = bytes_uploaded / (1024 * 1024)
                                total_mb = total_bytes / (1024 * 1024)
                                await send_progress(
                                    f"Upload progress: {percent}% ({size_mb:.1f}/{total_mb:.1f} MB)"
                                )

                    success_upload, error = await self.upload_file_with_progress(
                        steamcmd_local_path,
                        remote_steamcmd_path,
                        server,
                        progress_callback=upload_progress_callback,
                    )

                    if not success_upload:
                        raise Exception(f"Failed to upload SteamCMD: {error}")

                    await send_progress("✓ SteamCMD uploaded successfully")

                finally:
                    # Clean up panel temp directory
                    if steamcmd_local_path:
                        await _cleanup_local_download_dir(download_dir, panel_temp_dir)
            else:
                # Original Mode: Download directly on remote server
                download_cmd = f"wget --progress=dot:mega https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz -O {steamcmd_dir}/steamcmd_linux.tar.gz"
                success, stdout, stderr = await self.execute_command_streaming(
                    download_cmd, output_callback=send_progress, timeout=300
                )
                if not success:
                    # Check if file was downloaded successfully despite non-zero exit code
                    check_cmd = f"test -f {steamcmd_dir}/steamcmd_linux.tar.gz && echo 'exists'"
                    check_success, check_stdout, _ = await self.execute_command(check_cmd)
                    if not check_success or "exists" not in check_stdout:
                        return (
                            False,
                            f"SteamCMD download failed: {stderr if stderr else 'Download incomplete'}",
                        )
                    # File exists, continue despite wget exit code
                    await send_progress("✓ SteamCMD download completed (file verified)")
                else:
                    await send_progress("✓ SteamCMD downloaded successfully")

            # Extract SteamCMD
            await send_progress("Extracting SteamCMD...")
            success, stdout, stderr = await self.execute_command(
                f"tar -xzf {steamcmd_dir}/steamcmd_linux.tar.gz -C {steamcmd_dir}", timeout=120
            )
            if not success:
                return False, f"SteamCMD extraction failed: {stderr}"
            await send_progress("✓ SteamCMD extracted successfully")

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
                executable_exists, executable_path = await self._cs2_executable_exists_connected(
                    server
                )
                if executable_exists:
                    await send_progress(f"✓ CS2 executable verified: {executable_path}")
                else:
                    await send_progress(f"✗ CS2 executable is still missing: {executable_path}")
                return executable_exists

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
            steam_sdk_dir = f"/home/{server.ssh_user}/.steam/sdk64"
            mkdir_cmd = f"mkdir -p {steam_sdk_dir}"
            await self.execute_command(mkdir_cmd)

            # Create symlink to steamclient.so
            # This fixes: "Failed to load module '/home/user/.steam/sdk64/steamclient.so'"
            steamclient_source = f"{steamcmd_dir}/linux64/steamclient.so"
            steamclient_target = f"{steam_sdk_dir}/steamclient.so"
            symlink_cmd = f"ln -sf {steamclient_source} {steamclient_target}"
            symlink_success, _, _ = await self.execute_command(symlink_cmd)

            if symlink_success:
                await send_progress("✓ steamclient.so symlink created successfully")
            else:
                await send_progress(
                    "⚠ Warning: Could not create steamclient.so symlink (may cause startup issues)"
                )

                # CS2 executable exists, installation successful despite exit code
                await send_progress("=" * 60)
                await send_progress("✓ CS2 server installed successfully (verified)")
                await send_progress("=" * 60)
                return True, "CS2 server deployed successfully"

            await send_progress("=" * 60)
            await send_progress("✓ CS2 server installed successfully!")
            await send_progress("=" * 60)

            # Deploy auto-restart wrapper script
            await send_progress("=" * 60)
            await send_progress("Deploying auto-restart wrapper script...")
            await send_progress("=" * 60)

            autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"

            # Read the autorestart script content
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            local_script_path = os.path.join(script_dir, "scripts", "cs2_autorestart.sh")

            try:
                async with await anyio.open_file(local_script_path, "r") as script_file:
                    script_content = await script_file.read()

                # Create the script on remote server
                create_script_cmd = (
                    f"cat > {autorestart_script_path} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT"
                )
                success, stdout, stderr = await self.execute_command(create_script_cmd, timeout=10)

                if not success:
                    await send_progress(f"⚠ Warning: Could not deploy autorestart script: {stderr}")
                else:
                    # Make script executable
                    chmod_script_cmd = f"chmod +x {autorestart_script_path}"
                    await self.execute_command(chmod_script_cmd)
                    await send_progress("✓ Auto-restart wrapper script deployed successfully")
            except Exception as e:
                await send_progress(f"⚠ Warning: Could not deploy autorestart script: {str(e)}")

            await send_progress("=" * 60)
            await send_progress("✓ Deployment completed successfully!")
            await send_progress("=" * 60)

            return True, "CS2 server deployed successfully"

        except Exception as e:
            await send_progress(f"Deployment error: {str(e)}")
            return False, f"Deployment error: {str(e)}"
        finally:
            await self.disconnect()

    async def perform_server_selfcheck(
        self, server: Server, progress_callback=None
    ) -> Tuple[bool, str]:
        """
        Perform universal self-checks on the CS2 server and automatically fix common issues.

        Checks performed:
        - CS2 executable exists and has proper permissions
        - steamclient.so symlink exists and is valid
        - gameinfo.gi is properly configured for Metamod (if installed)
        - Auto-restart script is deployed

        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates

        Returns:
            Tuple[bool, str]: (success, message)
        """

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        try:
            await send_progress("=" * 60)
            await send_progress("Performing server self-check and auto-fix...")
            await send_progress("=" * 60)

            issues_found = []
            issues_fixed = []

            # Check 1: CS2 executable exists and has proper permissions
            await send_progress("Checking CS2 executable...")
            cs2_executable = f"{server.game_directory}/cs2/game/bin/linuxsteamrt64/cs2"
            verify_cmd = f"test -f {cs2_executable} && echo 'exists'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)

            if not verify_success or "exists" not in verify_stdout:
                issues_found.append("CS2 executable not found")
                await send_progress("✗ CS2 executable not found - server may not be deployed")
            else:
                # Ensure executable has proper permissions
                chmod_cmd = f"chmod +x {cs2_executable}"
                chmod_success, _, _ = await self.execute_command(chmod_cmd)
                if chmod_success:
                    await send_progress("✓ CS2 executable found and permissions set")
                else:
                    await send_progress("⚠ CS2 executable found but could not set permissions")

            # Check 2: steamclient.so symlink
            await send_progress("Checking steamclient.so symlink...")
            steam_sdk_dir = f"/home/{server.ssh_user}/.steam/sdk64"
            steamclient_target = f"{steam_sdk_dir}/steamclient.so"

            check_cmd = f"test -L {steamclient_target} && test -e {steamclient_target} && echo 'valid' || echo 'missing'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)

            if "missing" in check_stdout or not check_success:
                issues_found.append("steamclient.so symlink missing or broken")
                await send_progress(
                    "✗ steamclient.so symlink missing or broken - attempting to fix..."
                )

                # Create directory
                mkdir_cmd = f"mkdir -p {steam_sdk_dir}"
                await self.execute_command(mkdir_cmd)

                # Find steamclient.so source
                steamcmd_dir = f"{server.game_directory}/steamcmd"
                steamclient_source = f"{steamcmd_dir}/linux64/steamclient.so"

                source_check = f"test -f {steamclient_source} && echo 'found' || echo 'notfound'"
                source_success, source_stdout, _ = await self.execute_command(source_check)

                if "found" in source_stdout:
                    # Create symlink
                    symlink_cmd = f"ln -sf {steamclient_source} {steamclient_target}"
                    symlink_success, _, _ = await self.execute_command(symlink_cmd)

                    if symlink_success:
                        issues_fixed.append("steamclient.so symlink")
                        await send_progress("✓ steamclient.so symlink created successfully")
                    else:
                        await send_progress("✗ Failed to create steamclient.so symlink")
                else:
                    await send_progress(
                        f"✗ steamclient.so source not found at {steamclient_source}"
                    )
            else:
                await send_progress("✓ steamclient.so symlink is valid")

            # Check 3: gameinfo.gi for Metamod
            await send_progress("Checking gameinfo.gi configuration...")
            cs2_dir = f"{server.game_directory}/cs2"
            gameinfo_path = f"{cs2_dir}/game/csgo/gameinfo.gi"
            metamod_dir = f"{cs2_dir}/game/csgo/addons/metamod"

            # Check if Metamod is installed
            check_mm_cmd = f"test -d {metamod_dir} && echo 'exists'"
            mm_exists_success, mm_exists_stdout, _ = await self.execute_command(check_mm_cmd)

            if mm_exists_success and "exists" in mm_exists_stdout:
                # Check if gameinfo.gi exists
                check_gi_cmd = f"test -f {gameinfo_path} && echo 'exists'"
                gi_exists_success, gi_exists_stdout, _ = await self.execute_command(check_gi_cmd)

                if gi_exists_success and "exists" in gi_exists_stdout:
                    # Check if Metamod is configured in gameinfo.gi
                    check_mm_line = f"grep -q 'addons/metamod' {gameinfo_path} && echo 'found' || echo 'notfound'"
                    check_line_success, check_line_stdout, _ = await self.execute_command(
                        check_mm_line
                    )

                    if "notfound" in check_line_stdout:
                        issues_found.append("Metamod not configured in gameinfo.gi")
                        await send_progress(
                            "✗ Metamod installed but not configured in gameinfo.gi - attempting to fix..."
                        )

                        # Backup gameinfo.gi
                        backup_cmd = (
                            f"cp {gameinfo_path} {gameinfo_path}.backup.$(date +%Y%m%d_%H%M%S)"
                        )
                        await self.execute_command(backup_cmd)

                        # Add Metamod to gameinfo.gi
                        sed_cmd = f"sed -i '/Game_LowViolence/a\\			Game\\tcsgo/addons/metamod' {gameinfo_path}"
                        sed_success, _, _ = await self.execute_command(sed_cmd)

                        if sed_success:
                            issues_fixed.append("gameinfo.gi Metamod configuration")
                            await send_progress("✓ Metamod added to gameinfo.gi successfully")
                        else:
                            await send_progress("✗ Failed to update gameinfo.gi automatically")
                    else:
                        await send_progress("✓ Metamod is properly configured in gameinfo.gi")
                else:
                    await send_progress(
                        "⚠ gameinfo.gi not found (Metamod installed but game not deployed)"
                    )
            else:
                await send_progress("✓ Metamod not installed - gameinfo.gi check skipped")

            # Check 4: Auto-restart script
            await send_progress("Checking auto-restart script...")
            autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"

            check_script_cmd = f"test -f {autorestart_script_path} && test -x {autorestart_script_path} && echo 'exists'"
            script_success, script_stdout, _ = await self.execute_command(check_script_cmd)

            if not script_success or "exists" not in script_stdout:
                issues_found.append("Auto-restart script not found or not executable")
                await send_progress("✗ Auto-restart script missing - attempting to deploy...")

                # Deploy the script
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                local_script_path = os.path.join(script_dir, "scripts", "cs2_autorestart.sh")

                try:
                    async with await anyio.open_file(local_script_path, "r") as script_file:
                        script_content = await script_file.read()

                    create_script_cmd = f"cat > {autorestart_script_path} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT"
                    deploy_success, _, _ = await self.execute_command(create_script_cmd, timeout=10)

                    if deploy_success:
                        chmod_script_cmd = f"chmod +x {autorestart_script_path}"
                        await self.execute_command(chmod_script_cmd)
                        issues_fixed.append("auto-restart script")
                        await send_progress("✓ Auto-restart script deployed successfully")
                    else:
                        await send_progress("✗ Failed to deploy auto-restart script")
                except Exception as e:
                    await send_progress(f"✗ Error deploying auto-restart script: {str(e)}")
            else:
                await send_progress("✓ Auto-restart script is deployed and executable")

            # Summary
            await send_progress("=" * 60)
            await send_progress("Self-check completed!")
            await send_progress("=" * 60)

            if issues_found:
                await send_progress(f"Issues found: {len(issues_found)}")
                for issue in issues_found:
                    await send_progress(f"  - {issue}")

            if issues_fixed:
                await send_progress(f"Issues fixed: {len(issues_fixed)}")
                for fix in issues_fixed:
                    await send_progress(f"  ✓ {fix}")

            if not issues_found:
                await send_progress("✓ No issues found - server is ready to start")
                return True, "Server self-check passed"
            elif len(issues_fixed) == len(issues_found):
                await send_progress("✓ All issues were automatically fixed")
                return True, "Server self-check completed with auto-fixes"
            else:
                unfixed = len(issues_found) - len(issues_fixed)
                await send_progress(f"⚠ {unfixed} issue(s) could not be automatically fixed")
                return False, f"{unfixed} issues remain"

        except Exception as e:
            await send_progress(f"Self-check error: {str(e)}")
            return False, f"Self-check error: {str(e)}"

    async def _running_server_session_managers(
        self,
        server: Server,
        timeout: int = 10,
    ) -> List[str]:
        """Find configured and legacy sessions for a server on this connection."""
        return await find_running_session_managers(
            self.execute_command,
            server.session_manager,
            session_name(server.id),
            timeout=timeout,
        )

    async def _configured_session_manager_available_connected(
        self,
        server: Server,
        timeout: int = 10,
    ) -> Tuple[bool, str]:
        """Check the selected manager without changing any existing session."""
        manager = normalize_session_manager(server.session_manager)
        available, _, _ = await self.execute_command(
            availability_command(manager),
            timeout=timeout,
        )
        if available:
            return True, f"{manager} is available"
        return False, (f"Selected session manager '{manager}' is not installed on the remote host")

    @classmethod
    def _cs2_executable_path(cls, server: Server) -> str:
        """Return the required Linux CS2 executable path for a server."""
        return posixpath.join(
            server.game_directory.rstrip("/"),
            cls.CS2_EXECUTABLE_RELATIVE_PATH,
        )

    async def _cs2_executable_exists_connected(
        self,
        server: Server,
        timeout: int = 10,
    ) -> Tuple[bool, str]:
        """Check the required executable using the current SSH connection."""
        executable_path = self._cs2_executable_path(server)
        success, stdout, _ = await self.execute_command(
            f"test -f {shlex.quote(executable_path)} && echo exists",
            timeout=timeout,
        )
        return success and stdout.strip() == "exists", executable_path

    @staticmethod
    def _missing_cs2_executable_message(executable_path: str) -> str:
        return f"CS2 可执行文件不存在：{executable_path}。请重新部署服务器或运行修复/验证。"

    async def check_session_manager_available(
        self,
        server: Server,
        timeout: int = 10,
    ) -> Tuple[bool, str]:
        """Check start/restart prerequisites before stopping a live server."""
        success, message = await self.connect(server)
        if not success:
            return False, f"Connection failed during start/restart preflight: {message}"

        try:
            executable_exists, executable_path = await self._cs2_executable_exists_connected(
                server, timeout=timeout
            )
            if not executable_exists:
                return False, self._missing_cs2_executable_message(executable_path)

            return await self._configured_session_manager_available_connected(
                server,
                timeout=timeout,
            )
        finally:
            await self.disconnect()

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
            sanitized_cmd = sanitize_sensitive_value(
                sanitized_cmd, server.server_password, "***PASSWORD***"
            )
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
        running = await self._steamcmd_session_running(server, manager)
        if running:
            await send_progress(
                f"Reattached to existing {running} session {name}. "
                "SteamCMD was already detached from SSH."
            )
        else:
            await self.execute_command(f"rm -f -- {shlex.quote(exit_path)}", timeout=10)
            payload = wrap_steamcmd_payload(command, exit_path)
            start_cmd = start_session_command(manager, name, payload)
            await send_progress(
                f"Starting SteamCMD in detached {manager} session {name} (survives SSH reconnect)."
            )
            success, stdout, stderr = await self.execute_command(start_cmd, timeout=30)
            if not success:
                return False, stdout, stderr or "Failed to start SteamCMD session"
            started = await self._steamcmd_session_running(server, manager)
            if started is None:
                exit_code = await self._read_steamcmd_exit_code(server)
                if exit_code is not None:
                    return (
                        exit_code == 0,
                        stdout,
                        "" if exit_code == 0 else f"SteamCMD exited {exit_code}",
                    )
                return False, stdout, stderr or "SteamCMD session did not start"

        await send_progress(f"Watching {manager} session {name}; waiting for SteamCMD output…")
        deadline = time.monotonic() + max(int(timeout), 60)
        last_capture = ""
        last_heartbeat = 0.0
        captured_chunks: list[str] = []
        while time.monotonic() < deadline:
            server_id = getattr(server, "id", None)
            if server_id is not None and await steamcmd_cancel_requested(server_id):
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
        max_retries: int = None,
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
            max_retries = await resolve_steamcmd_max_retries(getattr(server, "user_id", None))
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
            if server_id is not None and await steamcmd_cancel_requested(server_id):
                await send_progress("✗ SteamCMD force-stop requested; leaving this server's lock")
                return False, stdout, STEAMCMD_FORCE_TERMINATED

            try:
                if attempt > 0:
                    still_running = await self._steamcmd_session_running(server)
                    if still_running or await self._list_steamcmd_pids(server):
                        await send_progress(
                            "SteamCMD detached session is still running; "
                            "resuming the log instead of starting another download."
                        )
                    else:
                        await self._kill_steamcmd_processes(server, progress_callback)
                        delay = steamcmd_retry_delay_seconds(attempt, self.STEAMCMD_RETRY_DELAY)
                        await send_progress(
                            f"⏳ Auto-recover {attempt}/{max_retries} - "
                            f"waiting {int(delay)} seconds before retry..."
                        )
                        await asyncio.sleep(delay)
                        if server_id is not None and await steamcmd_cancel_requested(server_id):
                            await send_progress("✗ SteamCMD force-stop requested during backoff")
                            return False, stdout, STEAMCMD_FORCE_TERMINATED
                        await send_progress(
                            f"🔄 Starting recovery attempt {attempt}/{max_retries}..."
                        )

                success, stdout, stderr = await self._stream_steamcmd_with_heartbeat(
                    command,
                    server,
                    send_progress,
                    timeout=timeout,
                )

                if not success:
                    retryable = is_steamcmd_failure_retryable(stdout, stderr)
                    retry_reason = stderr or stdout or "SteamCMD exited unexpectedly"
            except asyncio.TimeoutError:
                success = False
                stderr = "Command timeout"
                retry_reason = stderr
                retryable = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                success = False
                stderr = str(exc)
                retry_reason = f"Unexpected error: {exc}"
                retryable = True

            completion_verified = await completion_is_verified()
            if completion_verified is True:
                if not success:
                    await send_progress(
                        "✓ SteamCMD exited with an error, but the required "
                        "deployment file was verified"
                    )
                elif attempt > 0:
                    await send_progress(
                        f"✓ SteamCMD command succeeded on retry attempt {attempt}/{max_retries}"
                    )
                return True, stdout, stderr

            if completion_verified is False:
                artifact_error = "Required deployment file is missing after SteamCMD exit"
                stderr = f"{stderr}; {artifact_error}" if stderr else artifact_error
                retry_reason = "Required deployment file is missing"
                # Incomplete downloads (including a zero exit) are recoverable.
                # Permanent errors such as a full disk are not.
                if success or is_steamcmd_failure_retryable(stdout, stderr):
                    retryable = True
                else:
                    retryable = False
            elif success:
                if attempt > 0:
                    await send_progress(
                        f"✓ SteamCMD command succeeded on retry attempt {attempt}/{max_retries}"
                    )
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

    async def update_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """Update CS2 server using SteamCMD (without validation)"""
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        try:
            await send_progress("Starting server update...")

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
                        f"Server update aborted before stopping: {manager_message}. "
                        "The existing game session was left running."
                    )
                await send_progress(
                    f"Server is running in {', '.join(running_managers)}, stopping before update..."
                )
                all_stopped, _ = await self._stop_server_sessions_connected(
                    server,
                    progress_callback=progress_callback,
                    retries=3,
                )
                if not all_stopped:
                    return False, (
                        "Server update aborted because the existing "
                        "screen/tmux session could not be stopped"
                    )
                await send_progress("✓ Server stopped successfully")

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

            # Restart server if it was running before
            if was_running:
                await send_progress("Restarting server...")
                # Actually restart the server instead of just suggesting it
                restart_success, restart_msg = await self.start_server(server, progress_callback)
                if restart_success:
                    await send_progress("✓ Server restarted successfully after update")
                else:
                    await send_progress(f"✗ Failed to restart server after update: {restart_msg}")
                    return (
                        False,
                        f"Server files updated, but failed to restore the running server: {restart_msg}",
                    )

            if was_running:
                return True, "Server updated and restored to running state successfully"
            return True, "Server updated successfully; server remained stopped"

        except Exception as e:
            await send_progress(f"Update error: {str(e)}")
            return False, f"Update error: {str(e)}"
        finally:
            await self.disconnect()

    async def validate_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """Update and validate CS2 server files using SteamCMD"""
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
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
