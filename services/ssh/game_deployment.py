"""Game operations for SSHManager."""

# ruff: noqa: F403,F405

from services.host_initialization import SshManagerHostRunner, ensure_steamcmd_packages
from services.steamcmd_guard import (
    cs2_deploy_steamcmd_failure_message,
)
from services.steamcmd_retry import (
    resolve_steamcmd_max_retries,
)
from services.system_dependencies import (
    APT_RETRY_ATTEMPTS,
    APT_RETRY_DELAYS_SECONDS,
    STEAMCMD_REQUIRED_PACKAGES,
    apt_get_command,
)

from .common import *
from .common import _cleanup_local_download_dir


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

    async def deploy_cs2_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:  # noqa: C901 - deployment protocol.
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
