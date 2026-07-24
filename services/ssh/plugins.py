"""Plugins operations for SSHManager."""

# ruff: noqa: F403,F405

from .common import *


class PluginOperationsMixin:
    """Internal plugins behavior; instantiate through SSHManager."""

    async def install_metamod(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Install Metamod:Source 2.0 for CS2 server

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
            await send_progress("=" * 60)
            await send_progress("Installing Metamod:Source 2.0 for CS2...")
            await send_progress("=" * 60)

            # Check if CS2 is installed
            cs2_dir = f"{server.game_directory}/cs2"
            check_cmd = f"test -d {cs2_dir} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)

            if not check_success or "exists" not in check_stdout:
                return False, "CS2 server not found. Please deploy the server first."

            await send_progress("✓ CS2 server directory found")

            # Get latest Metamod dev build from sourcemm/GitHub.
            await send_progress("Fetching latest Metamod:Source dev build...")
            fetch_success, metamod_url = await self._fetch_latest_metamod_url(send_progress)

            if not fetch_success:
                return False, metamod_url

            metamod_url = metamod_url.strip()
            await send_progress(f"✓ Found latest version: {metamod_url}")

            # Create temp directory for download
            temp_dir = f"/tmp/metamod_install_{server.id}"
            await send_progress(f"Creating temporary directory: {temp_dir}")
            await self.execute_command(f"mkdir -p {temp_dir}")

            # Check if panel proxy mode is enabled
            if server.use_panel_proxy:
                # Panel Proxy Mode: Download to panel server first, then upload via SFTP
                await send_progress("Using panel server proxy mode for Metamod download...")

                panel_archive_path = None
                try:
                    # Create temp directory on panel server
                    panel_temp_dir = os.path.join(
                        tempfile.gettempdir(), f"cs2_panel_proxy_metamod_{server.user_id}"
                    )
                    os.makedirs(panel_temp_dir, exist_ok=True)

                    # Create unique subdirectory
                    download_id = str(uuid.uuid4())
                    download_dir = os.path.join(panel_temp_dir, download_id)
                    os.makedirs(download_dir, exist_ok=True)

                    panel_archive_path = os.path.join(download_dir, "metamod.tar.gz")

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

                    actual_download_url = self._apply_github_download_proxy(
                        metamod_url, server.github_proxy
                    )
                    if actual_download_url != metamod_url:
                        await send_progress("Using GitHub proxy for download")

                    success_download, error = await self.http_resource.download_file(
                        actual_download_url,
                        panel_archive_path,
                        timeout=180,
                        progress_callback=download_progress_callback,
                    )

                    if not success_download:
                        raise Exception(f"Failed to download Metamod: {error}")

                    # Verify file size
                    file_size = os.path.getsize(panel_archive_path)
                    if file_size < 1000:
                        raise Exception("Downloaded file is too small or empty")

                    await send_progress(
                        f"Download complete ({file_size / (1024 * 1024):.2f} MB), uploading to server..."
                    )

                    # Upload to remote server via SFTP
                    remote_archive_path = f"{temp_dir}/metamod.tar.gz"

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
                        panel_archive_path,
                        remote_archive_path,
                        server,
                        progress_callback=upload_progress_callback,
                    )

                    if not success_upload:
                        raise Exception(f"Failed to upload Metamod: {error}")

                    await send_progress("✓ Metamod uploaded successfully")

                finally:
                    # Clean up panel temp directory
                    if panel_archive_path:
                        await _cleanup_local_download_dir(download_dir, panel_temp_dir)
            else:
                # Original Mode: Download directly on remote server
                actual_download_url = self._apply_github_download_proxy(
                    metamod_url, server.github_proxy
                )
                if actual_download_url != metamod_url:
                    await send_progress("Using GitHub proxy for download")

                # Download Metamod
                await send_progress(f"Downloading Metamod from {metamod_url}...")
                # Use curl as fallback if wget doesn't work well, with better error handling
                download_url_arg = shlex.quote(actual_download_url)
                download_cmd = f"curl -L -o {temp_dir}/metamod.tar.gz {download_url_arg} || wget --no-check-certificate -O {temp_dir}/metamod.tar.gz {download_url_arg}"
                success, stdout, stderr = await self.execute_command_streaming(
                    download_cmd, output_callback=send_progress, timeout=180
                )

                # Always verify the file was downloaded regardless of exit code
                check_cmd = f"test -f {temp_dir}/metamod.tar.gz && echo 'exists'"
                check_success, check_stdout, _ = await self.execute_command(check_cmd)

                if not check_success or "exists" not in check_stdout:
                    await self.execute_command(f"rm -rf {temp_dir}")
                    error_detail = (
                        f"Download failed. stderr: {stderr[:500] if stderr else 'No error output'}"
                    )
                    return False, f"Metamod download failed: {error_detail}"

                # Check file size to ensure it's not empty
                size_cmd = f"stat -f%z {temp_dir}/metamod.tar.gz 2>/dev/null || stat -c%s {temp_dir}/metamod.tar.gz 2>/dev/null"
                size_success, size_out, _ = await self.execute_command(size_cmd)
                if size_success and size_out.strip():
                    file_size = int(size_out.strip())
                    if file_size < 1000:  # Less than 1KB is probably an error
                        await self.execute_command(f"rm -rf {temp_dir}")
                        return (
                            False,
                            f"Downloaded file is too small ({file_size} bytes). Download may have failed.",
                        )
                    await send_progress(f"✓ Downloaded {file_size} bytes")

                await send_progress("✓ Metamod downloaded successfully")

            # Extract Metamod to CS2 csgo directory (tar contains addons/metamod structure)
            csgo_dir = f"{cs2_dir}/game/csgo"
            await send_progress(f"Extracting Metamod to {csgo_dir}...")
            extract_cmd = f"tar -xzf {temp_dir}/metamod.tar.gz -C {csgo_dir}"
            success, stdout, stderr = await self.execute_command(extract_cmd, timeout=60)

            if not success:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, f"Metamod extraction failed: {stderr}"

            await send_progress("✓ Metamod extracted successfully")

            # Modify gameinfo.gi to add Metamod
            gameinfo_path = f"{cs2_dir}/game/csgo/gameinfo.gi"
            await send_progress("Updating gameinfo.gi...")

            # Check if gameinfo.gi exists
            check_cmd = f"test -f {gameinfo_path} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)

            if not check_success or "exists" not in check_stdout:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, "gameinfo.gi not found. Server may not be properly installed."

            # Check if Metamod is already in gameinfo.gi
            check_mm_cmd = (
                f"grep -q 'addons/metamod' {gameinfo_path} && echo 'found' || echo 'notfound'"
            )
            check_success, check_stdout, _ = await self.execute_command(check_mm_cmd)

            if "found" in check_stdout:
                await send_progress("✓ Metamod already configured in gameinfo.gi")
            else:
                # Backup gameinfo.gi
                backup_cmd = f"cp {gameinfo_path} {gameinfo_path}.backup"
                await self.execute_command(backup_cmd)
                await send_progress("✓ Created backup of gameinfo.gi")

                # Add Metamod to gameinfo.gi
                # We need to add "Game csgo/addons/metamod" after the Game_LowViolence line
                sed_cmd = f"sed -i '/Game_LowViolence/a\\			Game\\tcsgo/addons/metamod' {gameinfo_path}"
                success, stdout, stderr = await self.execute_command(sed_cmd)

                if not success:
                    await send_progress("⚠ Warning: Could not automatically update gameinfo.gi")
                    await send_progress(
                        "You may need to manually add 'Game csgo/addons/metamod' to gameinfo.gi"
                    )
                else:
                    await send_progress("✓ gameinfo.gi updated successfully")

            # Clean up temp directory
            await self.execute_command(f"rm -rf {temp_dir}")

            # Verify installation
            metamod_dir = f"{cs2_dir}/game/csgo/addons/metamod"
            verify_cmd = f"test -d {metamod_dir} && echo 'installed'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)

            if verify_success and "installed" in verify_stdout:
                await send_progress("=" * 60)
                await send_progress("✓ Metamod:Source installed successfully!")
                await send_progress("=" * 60)
                await send_progress(
                    "NOTE: You may need to restart your server for changes to take effect."
                )
                await send_progress(
                    "After server updates, you may need to re-add the Metamod line to gameinfo.gi"
                )
                return True, "Metamod:Source installed successfully"
            else:
                return False, "Metamod installation verification failed"

        except Exception as e:
            await send_progress(f"Installation error: {str(e)}")
            return False, f"Installation error: {str(e)}"
        finally:
            await self.disconnect()

    async def install_counterstrikesharp(
        self, server: Server, progress_callback=None
    ) -> Tuple[bool, str]:
        """
        Install CounterStrikeSharp for CS2 server

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
            await send_progress("=" * 60)
            await send_progress("Installing CounterStrikeSharp for CS2...")
            await send_progress("=" * 60)

            # Check if CS2 is installed
            cs2_dir = f"{server.game_directory}/cs2"
            check_cmd = f"test -d {cs2_dir} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)

            if not check_success or "exists" not in check_stdout:
                return False, "CS2 server not found. Please deploy the server first."

            await send_progress("✓ CS2 server directory found")

            # Check if Metamod is installed (required for CounterStrikeSharp)
            metamod_dir = f"{cs2_dir}/game/csgo/addons/metamod"
            check_mm_cmd = f"test -d {metamod_dir} && echo 'exists'"
            check_mm_success, check_mm_stdout, _ = await self.execute_command(check_mm_cmd)

            if not check_mm_success or "exists" not in check_mm_stdout:
                await send_progress("⚠ Warning: Metamod not found. Installing Metamod first...")
                mm_success, mm_msg = await self.install_metamod(server, progress_callback)
                if not mm_success:
                    return False, f"Metamod installation failed: {mm_msg}"
            else:
                await send_progress("✓ Metamod already installed")

            # Get latest CounterStrikeSharp release from GitHub
            await send_progress("Fetching latest CounterStrikeSharp release from GitHub...")

            # GitHub API URL - DO NOT use proxy for API requests
            # Proxy services like ghfast.top only work for file downloads, not API
            api_url = "https://api.github.com/repos/roflmuffin/CounterStrikeSharp/releases/latest"
            # Note: server.github_proxy exists but is NOT used for API requests

            # Use GitHub API to get the latest release - specifically look for with-runtime-linux
            api_cmd = (
                f"curl -s {api_url} | "
                'grep -oP \'"browser_download_url": "\\K[^"]*counterstrikesharp-with-runtime-linux[^"]*\\.zip\' | head -1'
            )
            success, css_url, stderr = await self.execute_command(api_cmd, timeout=30)

            if not success or not css_url.strip():
                # Fallback: try to get any linux zip and filter
                await send_progress("⚠ Trying alternative API query...")
                alt_cmd = (
                    f"curl -s {api_url} | "
                    "grep '\"browser_download_url\"' | grep 'with-runtime-linux' | grep -oP 'https://[^\"]*\\.zip' | head -1"
                )
                success, css_url, _ = await self.execute_command(alt_cmd, timeout=30)

                if not success or not css_url.strip():
                    # Last fallback - construct URL from version tag
                    await send_progress(
                        "⚠ Could not fetch from GitHub API, constructing fallback URL..."
                    )
                    # Get the latest tag version
                    tag_cmd = (
                        f"curl -s {api_url} | grep '\"tag_name\"' | grep -oP 'v[0-9.]+' | head -1"
                    )
                    tag_success, tag, _ = await self.execute_command(tag_cmd, timeout=30)

                    if tag_success and tag.strip():
                        version = tag.strip().lstrip("v")
                        css_url = f"https://github.com/roflmuffin/CounterStrikeSharp/releases/download/{tag.strip()}/counterstrikesharp-with-runtime-linux-{version}.zip"
                        await send_progress(f"Using constructed URL for version {version}")
                    else:
                        return (
                            False,
                            "Could not determine CounterStrikeSharp version from GitHub API",
                        )
            else:
                css_url = css_url.strip()

            await send_progress(f"Download URL: {css_url}")

            # Create temp directory for download
            temp_dir = f"/tmp/css_install_{server.id}"
            await send_progress(f"Creating temporary directory: {temp_dir}")
            await self.execute_command(f"mkdir -p {temp_dir}")

            # Check if panel proxy mode is enabled
            if server.use_panel_proxy:
                # Panel Proxy Mode: Download to panel server first, then upload via SFTP
                await send_progress(
                    "Using panel server proxy mode for CounterStrikeSharp download..."
                )

                panel_archive_path = None
                try:
                    # Create temp directory on panel server
                    panel_temp_dir = os.path.join(
                        tempfile.gettempdir(), f"cs2_panel_proxy_css_{server.user_id}"
                    )
                    os.makedirs(panel_temp_dir, exist_ok=True)

                    # Create unique subdirectory
                    download_id = str(uuid.uuid4())
                    download_dir = os.path.join(panel_temp_dir, download_id)
                    os.makedirs(download_dir, exist_ok=True)

                    panel_archive_path = os.path.join(download_dir, "counterstrikesharp.zip")

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

                    success_download, error = await self.http_resource.download_file(
                        css_url,
                        panel_archive_path,
                        timeout=300,
                        progress_callback=download_progress_callback,
                    )

                    if not success_download:
                        raise Exception(f"Failed to download CounterStrikeSharp: {error}")

                    # Verify file size
                    file_size = os.path.getsize(panel_archive_path)
                    if file_size < 10000:
                        raise Exception(f"Downloaded file is too small ({file_size} bytes)")

                    await send_progress(
                        f"Download complete ({file_size / (1024 * 1024):.2f} MB), uploading to server..."
                    )

                    # Upload to remote server via SFTP
                    remote_archive_path = f"{temp_dir}/counterstrikesharp.zip"

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
                        panel_archive_path,
                        remote_archive_path,
                        server,
                        progress_callback=upload_progress_callback,
                    )

                    if not success_upload:
                        raise Exception(f"Failed to upload CounterStrikeSharp: {error}")

                    await send_progress("✓ CounterStrikeSharp uploaded successfully")

                finally:
                    # Clean up panel temp directory
                    if panel_archive_path:
                        await _cleanup_local_download_dir(download_dir, panel_temp_dir)
            else:
                # Original Mode: Download directly on remote server (use GitHub proxy if configured)
                # Apply GitHub proxy to download URL if configured
                actual_download_url = css_url
                if server.github_proxy and server.github_proxy.strip():
                    proxy_base = server.github_proxy.strip().rstrip("/")
                    actual_download_url = f"{proxy_base}/{css_url}"
                    await send_progress("Using GitHub proxy for download")

                # Download CounterStrikeSharp
                await send_progress("Downloading CounterStrikeSharp...")
                # Use curl as fallback if wget doesn't work well
                download_cmd = f"curl -L -o {temp_dir}/counterstrikesharp.zip {actual_download_url} || wget --no-check-certificate -O {temp_dir}/counterstrikesharp.zip {actual_download_url}"
                success, stdout, stderr = await self.execute_command_streaming(
                    download_cmd,
                    output_callback=send_progress,
                    timeout=300,  # 5 minutes for larger download
                )

                # Always verify the file was downloaded
                check_cmd = f"test -f {temp_dir}/counterstrikesharp.zip && echo 'exists'"
                check_success, check_stdout, _ = await self.execute_command(check_cmd)

                if not check_success or "exists" not in check_stdout:
                    await self.execute_command(f"rm -rf {temp_dir}")
                    error_detail = (
                        f"Download failed. stderr: {stderr[:500] if stderr else 'No error output'}"
                    )
                    return False, f"CounterStrikeSharp download failed: {error_detail}"

                # Check file size
                size_cmd = f"stat -f%z {temp_dir}/counterstrikesharp.zip 2>/dev/null || stat -c%s {temp_dir}/counterstrikesharp.zip 2>/dev/null"
                size_success, size_out, _ = await self.execute_command(size_cmd)
                if size_success and size_out.strip():
                    file_size = int(size_out.strip())
                    if file_size < 10000:  # Less than 10KB is probably an error
                        await self.execute_command(f"rm -rf {temp_dir}")
                        return (
                            False,
                            f"Downloaded file is too small ({file_size} bytes). Download may have failed.",
                        )
                    await send_progress(f"✓ Downloaded {file_size} bytes")

                await send_progress("✓ CounterStrikeSharp downloaded successfully")

            # Check if unzip is available and try to install if missing
            check_unzip = "command -v unzip"
            unzip_success, _, _ = await self.execute_command(check_unzip)

            if not unzip_success:
                await send_progress("⚠ Warning: unzip not found. Attempting to install...")

                # Check package manager
                check_apt = "command -v apt-get > /dev/null && echo 'apt' || echo 'none'"
                _, pkg_mgr, _ = await self.execute_command(check_apt)

                if "apt" in pkg_mgr:
                    # Try to install without sudo first
                    install_cmd = "apt-get update && apt-get install -y unzip"
                    success, stdout, stderr = await self.execute_command(install_cmd, timeout=120)

                    if not success:
                        # Try with sudo if available
                        if server.sudo_password:
                            await send_progress("Trying to install unzip with sudo...")
                            install_cmd = "apt-get update && apt-get install -y unzip"
                            success, stdout, stderr = await self.execute_sudo_command(
                                install_cmd,
                                server.sudo_password,
                                timeout=120,
                            )

                            if success:
                                await send_progress("✓ unzip installed successfully")
                            else:
                                await self.execute_command(f"rm -rf {temp_dir}")
                                return (
                                    False,
                                    f"Could not install unzip. Please run: sudo apt-get install unzip\nError: {stderr[:200]}",
                                )
                        else:
                            await self.execute_command(f"rm -rf {temp_dir}")
                            return (
                                False,
                                "unzip not found and no sudo password provided. Please install unzip: sudo apt-get install unzip",
                            )
                    else:
                        await send_progress("✓ unzip installed successfully")

                    # Verify unzip is now available
                    unzip_success, _, _ = await self.execute_command(check_unzip)
                    if not unzip_success:
                        await self.execute_command(f"rm -rf {temp_dir}")
                        return (
                            False,
                            "unzip installation completed but command still not found. Please check system PATH.",
                        )
                else:
                    await self.execute_command(f"rm -rf {temp_dir}")
                    return (
                        False,
                        "unzip not found and package manager not detected. Please install unzip manually.",
                    )
            else:
                await send_progress("✓ unzip is available")

            # Extract CounterStrikeSharp to CS2 directory
            # The zip contains an 'addons' folder that should merge with the existing addons
            await send_progress("Extracting CounterStrikeSharp...")
            extract_cmd = f"unzip -o {temp_dir}/counterstrikesharp.zip -d {cs2_dir}/game/csgo/"
            success, stdout, stderr = await self.execute_command(extract_cmd, timeout=120)

            if not success:
                await self.execute_command(f"rm -rf {temp_dir}")
                extraction_error = stderr or stdout or "unzip returned a non-zero status"
                return False, f"CounterStrikeSharp extraction failed: {extraction_error}"

            # Check if extraction actually succeeded by checking the directory
            verify_extract = (
                f"test -d {cs2_dir}/game/csgo/addons/counterstrikesharp && echo 'extracted'"
            )
            verify_success, verify_out, _ = await self.execute_command(verify_extract)

            if not verify_success or "extracted" not in verify_out:
                await self.execute_command(f"rm -rf {temp_dir}")
                return (
                    False,
                    f"CounterStrikeSharp extraction failed: {stderr if stderr else 'Directory not created'}",
                )

            await send_progress("✓ CounterStrikeSharp extracted successfully")

            # Clean up temp directory
            await self.execute_command(f"rm -rf {temp_dir}")

            # Verify installation
            css_dir = f"{cs2_dir}/game/csgo/addons/counterstrikesharp"
            verify_cmd = f"test -d {css_dir} && echo 'installed'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)

            if verify_success and "installed" in verify_stdout:
                await send_progress("=" * 60)
                await send_progress("✓ CounterStrikeSharp installed successfully!")
                await send_progress("=" * 60)
                await send_progress(
                    "NOTE: You need to restart your server for changes to take effect."
                )
                await send_progress(
                    "After restart, use 'meta list' and 'css_plugins list' to verify."
                )
                return True, "CounterStrikeSharp installed successfully"
            else:
                return False, "CounterStrikeSharp installation verification failed"

        except Exception as e:
            await send_progress(f"Installation error: {str(e)}")
            return False, f"Installation error: {str(e)}"
        finally:
            await self.disconnect()

    async def update_metamod(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Update Metamod:Source to the latest version

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

        await send_progress("Updating Metamod:Source to latest version...")
        await send_progress("This will reinstall Metamod with the latest version.")

        # Just reinstall - this will update to the latest version
        return await self.install_metamod(server, progress_callback)

    async def update_counterstrikesharp(
        self, server: Server, progress_callback=None
    ) -> Tuple[bool, str]:
        """
        Update CounterStrikeSharp to the latest version

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

        await send_progress("Updating CounterStrikeSharp to latest version...")
        await send_progress("This will reinstall CounterStrikeSharp with the latest version.")

        # Just reinstall - this will update to the latest version
        return await self.install_counterstrikesharp(server, progress_callback)

    async def install_cs2fixes(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Install CS2Fixes for CS2 server

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
            await send_progress("=" * 60)
            await send_progress("Installing CS2Fixes...")
            await send_progress("=" * 60)

            # Check if CS2 is installed
            cs2_dir = f"{server.game_directory}/cs2"
            check_cmd = f"test -d {cs2_dir} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)

            if not check_success or "exists" not in check_stdout:
                return False, "CS2 server not found. Please deploy the server first."

            await send_progress("✓ CS2 server directory found")

            # Check if Metamod is installed (required for CS2Fixes)
            metamod_dir = f"{cs2_dir}/game/csgo/addons/metamod"
            check_mm_cmd = f"test -d {metamod_dir} && echo 'exists'"
            check_mm_success, check_mm_stdout, _ = await self.execute_command(check_mm_cmd)

            if not check_mm_success or "exists" not in check_mm_stdout:
                await send_progress("⚠ Warning: Metamod not found. Installing Metamod first...")
                mm_success, mm_msg = await self.install_metamod(server, progress_callback)
                if not mm_success:
                    return False, f"Metamod installation failed: {mm_msg}"
            else:
                await send_progress("✓ Metamod:Source found")

            # Get latest CS2Fixes version from GitHub releases
            await send_progress("Fetching latest CS2Fixes version from GitHub...")

            # Use helper function with fallback strategies
            pattern = '"browser_download_url": "[^"]*CS2Fixes-[^"]*-linux\\.tar\\.gz"'
            fetch_success, cs2fixes_url = await self._fetch_github_release_url(
                "Source2ZE/CS2Fixes", pattern, progress_callback, server.github_proxy
            )

            if not fetch_success:
                return False, cs2fixes_url  # cs2fixes_url contains error message

            await send_progress(f"✓ Found latest version: {cs2fixes_url}")

            # Create temp directory for download
            temp_dir = f"/tmp/cs2fixes_install_{server.id}"
            await send_progress(f"Creating temporary directory: {temp_dir}")
            await self.execute_command(f"mkdir -p {temp_dir}")

            # Check if panel proxy mode is enabled
            if server.use_panel_proxy:
                # Panel Proxy Mode: Download to panel server first, then upload via SFTP
                await send_progress("Using panel server proxy mode for CS2Fixes download...")

                panel_archive_path = None
                try:
                    # Create temp directory on panel server
                    panel_temp_dir = os.path.join(
                        tempfile.gettempdir(), f"cs2_panel_proxy_cs2fixes_{server.user_id}"
                    )
                    os.makedirs(panel_temp_dir, exist_ok=True)

                    # Create unique subdirectory
                    download_id = str(uuid.uuid4())
                    download_dir = os.path.join(panel_temp_dir, download_id)
                    os.makedirs(download_dir, exist_ok=True)

                    panel_archive_path = os.path.join(download_dir, "cs2fixes.tar.gz")

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

                    # Apply GitHub proxy to download URL if configured
                    actual_download_url = cs2fixes_url
                    if server.github_proxy and server.github_proxy.strip():
                        proxy_base = server.github_proxy.strip().rstrip("/")
                        actual_download_url = f"{proxy_base}/{cs2fixes_url}"
                        await send_progress("Using GitHub proxy for download")

                    success_download, error = await self.http_resource.download_file(
                        actual_download_url,
                        panel_archive_path,
                        timeout=300,
                        progress_callback=download_progress_callback,
                    )

                    if not success_download:
                        raise Exception(f"Failed to download CS2Fixes: {error}")

                    # Verify file size
                    file_size = os.path.getsize(panel_archive_path)
                    if file_size < self.MIN_EXPECTED_FILE_SIZE:
                        raise Exception(f"Downloaded file is too small ({file_size} bytes)")

                    await send_progress(
                        f"Download complete ({file_size / (1024 * 1024):.2f} MB), uploading to server..."
                    )

                    # Upload to remote server via SFTP
                    remote_archive_path = f"{temp_dir}/cs2fixes.tar.gz"

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
                        panel_archive_path,
                        remote_archive_path,
                        server,
                        progress_callback=upload_progress_callback,
                    )

                    if not success_upload:
                        raise Exception(f"Failed to upload CS2Fixes: {error}")

                    await send_progress("✓ CS2Fixes uploaded successfully")

                finally:
                    # Clean up panel temp directory
                    if panel_archive_path:
                        await _cleanup_local_download_dir(download_dir, panel_temp_dir)
            else:
                # Original Mode: Download directly on remote server (use GitHub proxy if configured)
                # Apply GitHub proxy to download URL if configured
                actual_download_url = cs2fixes_url
                if server.github_proxy and server.github_proxy.strip():
                    proxy_base = server.github_proxy.strip().rstrip("/")
                    actual_download_url = f"{proxy_base}/{cs2fixes_url}"
                    await send_progress("Using GitHub proxy for download")

                # Download CS2Fixes
                await send_progress(f"Downloading CS2Fixes from {cs2fixes_url}...")
                download_cmd = f"curl -L -o {temp_dir}/cs2fixes.tar.gz {actual_download_url} || wget -O {temp_dir}/cs2fixes.tar.gz {actual_download_url}"
                success, stdout, stderr = await self.execute_command_streaming(
                    download_cmd, output_callback=send_progress, timeout=180
                )

                # Verify the file was downloaded
                check_cmd = f"test -f {temp_dir}/cs2fixes.tar.gz && echo 'exists'"
                check_success, check_stdout, _ = await self.execute_command(check_cmd)

                if not check_success or "exists" not in check_stdout:
                    await self.execute_command(f"rm -rf {temp_dir}")
                    error_detail = (
                        f"Download failed. stderr: {stderr[:500] if stderr else 'No error output'}"
                    )
                    return False, f"CS2Fixes download failed: {error_detail}"

                # Check file size to ensure it's not empty
                size_cmd = f"stat -f%z {temp_dir}/cs2fixes.tar.gz 2>/dev/null || stat -c%s {temp_dir}/cs2fixes.tar.gz 2>/dev/null"
                size_success, size_out, _ = await self.execute_command(size_cmd)
                if size_success and size_out.strip():
                    file_size = int(size_out.strip())
                    if file_size < self.MIN_EXPECTED_FILE_SIZE:
                        await self.execute_command(f"rm -rf {temp_dir}")
                        return (
                            False,
                            f"Downloaded file is too small ({file_size} bytes). Download may have failed.",
                        )
                    await send_progress(f"✓ Downloaded {file_size} bytes")

                await send_progress("✓ CS2Fixes downloaded successfully")

            # Extract CS2Fixes directly to CS2 directory
            csgo_dir = f"{cs2_dir}/game/csgo"
            await send_progress(f"Extracting and installing CS2Fixes to {csgo_dir}...")

            # The tar.gz contains multiple directories: addons, cfg, materials, particles, soundevents, sounds
            # Extract directly to csgo directory to install all files
            extract_cmd = f"tar -xzf {temp_dir}/cs2fixes.tar.gz -C {csgo_dir}"
            success, stdout, stderr = await self.execute_command(extract_cmd, timeout=60)

            if not success:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, f"CS2Fixes installation failed: {stderr}"

            await send_progress("✓ CS2Fixes files installed successfully")

            # Clean up temp directory
            await self.execute_command(f"rm -rf {temp_dir}")

            # Verify installation
            cs2fixes_dir = f"{csgo_dir}/addons/cs2fixes"
            verify_cmd = f"test -d {cs2fixes_dir} && echo 'installed'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)

            if verify_success and "installed" in verify_stdout:
                await send_progress("=" * 60)
                await send_progress("✓ CS2Fixes installed successfully!")
                await send_progress("=" * 60)
                await send_progress(
                    "NOTE: You need to restart your server for changes to take effect."
                )
                await send_progress("Use 'meta list' command to verify CS2Fixes is loaded.")
                return True, "CS2Fixes installed successfully"
            else:
                return False, "CS2Fixes installation verification failed"

        except Exception as e:
            await send_progress(f"Installation error: {str(e)}")
            return False, f"Installation error: {str(e)}"
        finally:
            await self.disconnect()

    async def update_cs2fixes(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Update CS2Fixes to the latest version

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

        await send_progress("Updating CS2Fixes to latest version...")
        await send_progress("This will reinstall CS2Fixes with the latest version.")

        # Just reinstall - this will update to the latest version
        return await self.install_cs2fixes(server, progress_callback)

    async def install_swiftly(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Install SwiftlyS2 framework for CS2 server

        SwiftlyS2 is a C#/Lua plugin framework that can run standalone (loader mode)
        without depending on Metamod updates.

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
            await send_progress("=" * 60)
            await send_progress("Installing SwiftlyS2...")
            await send_progress("=" * 60)

            # Check if CS2 is installed
            cs2_dir = f"{server.game_directory}/cs2"
            check_cmd = f"test -d {cs2_dir} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)

            if not check_success or "exists" not in check_stdout:
                return False, "CS2 server not found. Please deploy the server first."

            await send_progress("✓ CS2 server directory found")

            # Get latest SwiftlyS2 release from GitHub
            await send_progress("Fetching latest SwiftlyS2 release from GitHub...")

            # GitHub API URL - DO NOT use proxy for API requests
            api_url = "https://api.github.com/repos/swiftly-solution/swiftlys2/releases/latest"

            # Look for the linux with-runtimes zip (recommended for installation)
            api_cmd = (
                f"curl -s {api_url} | "
                "grep '\"browser_download_url\"' | grep 'linux' | grep 'with-runtimes' | grep -o 'https://[^\"]*\\.zip' | head -1"
            )
            success, swiftly_url, stderr = await self.execute_command(api_cmd, timeout=30)

            if not success or not swiftly_url.strip():
                # Fallback: try any linux zip
                await send_progress("⚠ Trying alternative API query...")
                alt_cmd = (
                    f"curl -s {api_url} | "
                    "grep '\"browser_download_url\"' | grep 'linux' | grep -o 'https://[^\"]*\\.zip' | head -1"
                )
                success, swiftly_url, _ = await self.execute_command(alt_cmd, timeout=30)

                if not success or not swiftly_url.strip():
                    return False, "Could not determine SwiftlyS2 download URL from GitHub API"

            swiftly_url = swiftly_url.strip()
            await send_progress(f"Download URL: {swiftly_url}")

            # Create temp directory for download
            temp_dir = f"/tmp/swiftly_install_{server.id}"
            await send_progress(f"Creating temporary directory: {temp_dir}")
            await self.execute_command(f"mkdir -p {temp_dir}")

            # Check if panel proxy mode is enabled
            if server.use_panel_proxy:
                # Panel Proxy Mode: Download to panel server first, then upload via SFTP
                await send_progress("Using panel server proxy mode for SwiftlyS2 download...")

                panel_archive_path = None
                try:
                    # Create temp directory on panel server
                    panel_temp_dir = os.path.join(
                        tempfile.gettempdir(), f"cs2_panel_proxy_swiftly_{server.user_id}"
                    )
                    os.makedirs(panel_temp_dir, exist_ok=True)

                    # Create unique subdirectory
                    download_id = str(uuid.uuid4())
                    download_dir = os.path.join(panel_temp_dir, download_id)
                    os.makedirs(download_dir, exist_ok=True)

                    panel_archive_path = os.path.join(download_dir, "swiftly.zip")

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

                    success_download, error = await self.http_resource.download_file(
                        swiftly_url,
                        panel_archive_path,
                        timeout=300,
                        progress_callback=download_progress_callback,
                    )

                    if not success_download:
                        raise Exception(f"Failed to download SwiftlyS2: {error}")

                    # Verify file size
                    file_size = os.path.getsize(panel_archive_path)
                    if file_size < 10000:
                        raise Exception(f"Downloaded file is too small ({file_size} bytes)")

                    await send_progress(
                        f"Download complete ({file_size / (1024 * 1024):.2f} MB), uploading to server..."
                    )

                    # Upload to remote server via SFTP
                    remote_archive_path = f"{temp_dir}/swiftly.zip"

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
                        panel_archive_path,
                        remote_archive_path,
                        server,
                        progress_callback=upload_progress_callback,
                    )

                    if not success_upload:
                        raise Exception(f"Failed to upload SwiftlyS2: {error}")

                    await send_progress("✓ SwiftlyS2 uploaded successfully")

                finally:
                    # Clean up panel temp directory
                    if panel_archive_path:
                        await _cleanup_local_download_dir(download_dir, panel_temp_dir)
            else:
                # Original Mode: Download directly on remote server (use GitHub proxy if configured)
                actual_download_url = swiftly_url
                if server.github_proxy and server.github_proxy.strip():
                    proxy_base = server.github_proxy.strip().rstrip("/")
                    actual_download_url = f"{proxy_base}/{swiftly_url}"
                    await send_progress("Using GitHub proxy for download")

                # Download SwiftlyS2
                await send_progress("Downloading SwiftlyS2...")
                download_cmd = f"curl -L -o {temp_dir}/swiftly.zip {actual_download_url} || wget --no-check-certificate -O {temp_dir}/swiftly.zip {actual_download_url}"
                success, stdout, stderr = await self.execute_command_streaming(
                    download_cmd,
                    output_callback=send_progress,
                    timeout=300,  # 5 minutes for larger download
                )

                # Verify the file was downloaded
                check_cmd = f"test -f {temp_dir}/swiftly.zip && echo 'exists'"
                check_success, check_stdout, _ = await self.execute_command(check_cmd)

                if not check_success or "exists" not in check_stdout:
                    await self.execute_command(f"rm -rf {temp_dir}")
                    error_detail = (
                        f"Download failed. stderr: {stderr[:500] if stderr else 'No error output'}"
                    )
                    return False, f"SwiftlyS2 download failed: {error_detail}"

                # Check file size
                size_cmd = f"stat -f%z {temp_dir}/swiftly.zip 2>/dev/null || stat -c%s {temp_dir}/swiftly.zip 2>/dev/null"
                size_success, size_out, _ = await self.execute_command(size_cmd)
                if size_success and size_out.strip():
                    file_size = int(size_out.strip())
                    if file_size < 10000:  # Less than 10KB is probably an error
                        await self.execute_command(f"rm -rf {temp_dir}")
                        return (
                            False,
                            f"Downloaded file is too small ({file_size} bytes). Download may have failed.",
                        )
                    await send_progress(f"✓ Downloaded {file_size} bytes")

                await send_progress("✓ SwiftlyS2 downloaded successfully")

            # Check if unzip is available and try to install if missing
            check_unzip = "command -v unzip"
            unzip_success, _, _ = await self.execute_command(check_unzip)

            if not unzip_success:
                await send_progress("⚠ Warning: unzip not found. Attempting to install...")

                # Check package manager
                check_apt = "command -v apt-get > /dev/null && echo 'apt' || echo 'none'"
                _, pkg_mgr, _ = await self.execute_command(check_apt)

                if "apt" in pkg_mgr:
                    install_cmd = "apt-get update && apt-get install -y unzip"
                    success, stdout, stderr = await self.execute_command(install_cmd, timeout=120)

                    if not success:
                        if server.sudo_password:
                            await send_progress("Trying to install unzip with sudo...")
                            install_cmd = "apt-get update && apt-get install -y unzip"
                            success, stdout, stderr = await self.execute_sudo_command(
                                install_cmd,
                                server.sudo_password,
                                timeout=120,
                            )

                            if success:
                                await send_progress("✓ unzip installed successfully")
                            else:
                                await self.execute_command(f"rm -rf {temp_dir}")
                                return (
                                    False,
                                    f"Could not install unzip. Please run: sudo apt-get install unzip\nError: {stderr[:200]}",
                                )
                        else:
                            await self.execute_command(f"rm -rf {temp_dir}")
                            return (
                                False,
                                "unzip not found and no sudo password provided. Please install unzip: sudo apt-get install unzip",
                            )
                    else:
                        await send_progress("✓ unzip installed successfully")

                    # Verify unzip is now available
                    unzip_success, _, _ = await self.execute_command(check_unzip)
                    if not unzip_success:
                        await self.execute_command(f"rm -rf {temp_dir}")
                        return (
                            False,
                            "unzip installation completed but command still not found. Please check system PATH.",
                        )
                else:
                    await self.execute_command(f"rm -rf {temp_dir}")
                    return (
                        False,
                        "unzip not found and package manager not detected. Please install unzip manually.",
                    )
            else:
                await send_progress("✓ unzip is available")

            # Extract SwiftlyS2 to CS2 directory
            # The zip contains a version-named top-level directory (e.g. swiftlys2-linux-v1.2.0-with-runtimes/addons/...)
            # We need to strip that top-level directory and copy the contents into csgo/
            csgo_dir = f"{cs2_dir}/game/csgo"
            extract_dir = f"{temp_dir}/extracted"
            await send_progress("Extracting SwiftlyS2...")
            extract_cmd = (
                f"mkdir -p {extract_dir} && unzip -o {temp_dir}/swiftly.zip -d {extract_dir}"
            )
            success, stdout, stderr = await self.execute_command(extract_cmd, timeout=120)

            if not success:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, f"SwiftlyS2 extraction failed: {stderr if stderr else 'unzip failed'}"

            # Find the addons directory inside the extracted content
            # maxdepth 2: addons/ may be at root or inside one top-level dir (e.g. swiftlys2-linux-vX/addons/)
            find_addons_cmd = (
                f"find {shlex.quote(extract_dir)} -maxdepth 2 -type d -name 'addons' | head -1"
            )
            _, addons_path, _ = await self.execute_command(find_addons_cmd)
            addons_path = addons_path.strip()

            if not addons_path or "/addons" not in addons_path:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, "SwiftlyS2 extraction failed: 'addons' directory not found in archive"

            # The parent of the addons dir contains what we need to copy into csgo_dir
            source_dir = addons_path.rsplit("/addons", 1)[0]
            await send_progress(f"Copying SwiftlyS2 files to {csgo_dir}...")
            copy_cmd = f"cp -rf {shlex.quote(source_dir)}/* {shlex.quote(csgo_dir)}/"
            success, stdout, stderr = await self.execute_command(copy_cmd, timeout=120)

            # Check if extraction actually succeeded by checking the directory
            verify_extract = f"test -d {csgo_dir}/addons/swiftlys2 && echo 'extracted'"
            verify_success, verify_out, _ = await self.execute_command(verify_extract)

            if not verify_success or "extracted" not in verify_out:
                await self.execute_command(f"rm -rf {temp_dir}")
                return (
                    False,
                    "SwiftlyS2 extraction failed: addons/swiftlys2 directory not created after copy",
                )

            await send_progress("✓ SwiftlyS2 extracted successfully")

            # Clean up temp directory
            await self.execute_command(f"rm -rf {temp_dir}")

            # Verify installation
            swiftly_dir = f"{csgo_dir}/addons/swiftlys2"
            verify_cmd = f"test -d {swiftly_dir} && echo 'installed'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)

            if verify_success and "installed" in verify_stdout:
                await send_progress("=" * 60)
                await send_progress("✓ SwiftlyS2 installed successfully!")
                await send_progress("=" * 60)
                await send_progress(
                    "NOTE: You need to restart your server for changes to take effect."
                )
                await send_progress(
                    "SwiftlyS2 is loaded via Metamod. Make sure Metamod is installed."
                )
                await send_progress(
                    "After restart, use 'sw' command in console to verify installation."
                )
                return True, "SwiftlyS2 installed successfully"
            else:
                return False, "SwiftlyS2 installation verification failed"

        except Exception as e:
            await send_progress(f"Installation error: {str(e)}")
            return False, f"Installation error: {str(e)}"
        finally:
            await self.disconnect()

    async def update_swiftly(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Update SwiftlyS2 to the latest version

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

        await send_progress("Updating SwiftlyS2 to latest version...")
        await send_progress("This will reinstall SwiftlyS2 with the latest version.")

        # Just reinstall - this will update to the latest version
        return await self.install_swiftly(server, progress_callback)

    async def backup_plugins(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Backup plugins (addons, cfg folders and gameinfo.gi file) to a timestamped tar.gz archive

        Creates backup at: {game_directory}/backups/YYYY-MM-DD-HHMMSS.tar.gz
        Backs up from: {game_directory}/cs2/game/csgo/
        - addons/ folder
        - cfg/ folder
        - gameinfo.gi file

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

        self.last_plugin_backup = None

        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

        try:
            await send_progress("=" * 60)
            await send_progress("Starting plugin backup...")
            await send_progress("=" * 60)

            # Use game_directory as the base for backups
            # For example: if game_directory is /home/cs2server/cs2kz, backups go to /home/cs2server/cs2kz/backups
            game_dir = server.game_directory.rstrip("/")

            # Check if CS2 is installed
            csgo_dir = f"{game_dir}/cs2/game/csgo"
            check_cmd = f"test -d {csgo_dir} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)

            if not check_success or "exists" not in check_stdout:
                return False, "CS2 server not found. Please deploy the server first."

            await send_progress(f"✓ CS2 server directory found: {csgo_dir}")

            # Create backups directory if it doesn't exist
            backups_dir = f"{game_dir}/backups"
            await send_progress(f"Creating backups directory: {backups_dir}")
            mkdir_cmd = f"mkdir -p {shlex.quote(backups_dir)}"
            mkdir_success, _, mkdir_stderr = await self.execute_command(mkdir_cmd)

            if not mkdir_success:
                error_msg = (
                    mkdir_stderr.strip()
                    if mkdir_stderr and mkdir_stderr.strip()
                    else "Failed to create backups directory"
                )
                await send_progress(f"✗ {error_msg}")
                return False, f"Failed to create backups directory: {error_msg}"

            await send_progress(f"✓ Backups directory ready: {backups_dir}")

            # Generate timestamp for backup filename
            # Get current time from server
            timestamp_cmd = "date '+%Y-%m-%d-%H%M%S'"
            ts_success, timestamp, _ = await self.execute_command(timestamp_cmd)

            if not ts_success or not timestamp.strip():
                # Fallback to local time if server time command fails
                timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            else:
                timestamp = timestamp.strip()

            backup_filename = f"{timestamp}.tar.gz"
            backup_path = f"{backups_dir}/{backup_filename}"
            file_size = None

            await send_progress(f"Backup will be saved to: {backup_path}")

            # Check which items exist before backing up
            items_to_backup = []

            # Check addons folder
            check_addons = f"test -d {csgo_dir}/addons && echo 'exists'"
            addons_success, addons_stdout, _ = await self.execute_command(check_addons)
            if addons_success and "exists" in addons_stdout:
                items_to_backup.append("addons")
                await send_progress("✓ Found: addons/")
            else:
                await send_progress("⚠ Warning: addons/ folder not found, skipping")

            # Check cfg folder
            check_cfg = f"test -d {csgo_dir}/cfg && echo 'exists'"
            cfg_success, cfg_stdout, _ = await self.execute_command(check_cfg)
            if cfg_success and "exists" in cfg_stdout:
                items_to_backup.append("cfg")
                await send_progress("✓ Found: cfg/")
            else:
                await send_progress("⚠ Warning: cfg/ folder not found, skipping")

            # Check gameinfo.gi file
            check_gameinfo = f"test -f {csgo_dir}/gameinfo.gi && echo 'exists'"
            gameinfo_success, gameinfo_stdout, _ = await self.execute_command(check_gameinfo)
            if gameinfo_success and "exists" in gameinfo_stdout:
                items_to_backup.append("gameinfo.gi")
                await send_progress("✓ Found: gameinfo.gi")
            else:
                await send_progress("⚠ Warning: gameinfo.gi file not found, skipping")

            if not items_to_backup:
                return (
                    False,
                    "No items found to backup. Please ensure the server is deployed and has plugins installed.",
                )

            # Create backup archive with the items that exist
            await send_progress(f"Creating backup archive with {len(items_to_backup)} item(s)...")

            # Create tar.gz directly in the backup directory
            # This is simpler and avoids issues with /tmp/ or moving files between directories
            tar_items = " ".join(items_to_backup)

            await send_progress(f"Creating compressed backup: {backup_path}")
            tar_cmd = (
                f"cd {shlex.quote(csgo_dir)} && tar -czf {shlex.quote(backup_path)} {tar_items}"
            )

            # Show the actual command for debugging
            await send_progress(f"[DEBUG] Executing command: {tar_cmd}")

            tar_success, tar_stdout, tar_stderr = await self.execute_command_streaming(
                tar_cmd,
                output_callback=send_progress,
                timeout=600,  # 10 minutes timeout for large backups
            )

            # Check if backup file was actually created (more reliable than exit code)
            # Tar can return non-zero exit codes for warnings (e.g., "file changed as we read it")
            # while still creating a valid backup. File existence is the true indicator of success.
            check_backup_exists = f"test -f {shlex.quote(backup_path)} && echo 'exists'"
            backup_exists_success, backup_exists_out, _ = await self.execute_command(
                check_backup_exists
            )
            backup_file_created = backup_exists_success and "exists" in backup_exists_out

            await send_progress(f"[DEBUG] Backup file created: {backup_file_created}")
            await send_progress(f"[DEBUG] Tar exit code successful: {tar_success}")

            # Prioritize file creation over exit code - if file exists, backup succeeded
            # This handles cases where tar returns warnings but still creates valid archives
            if not backup_file_created:
                # Provide detailed error message with command and all output
                error_detail = f"Command: {tar_cmd}\n"
                error_detail += f"Exit successful: {tar_success}\n"
                error_detail += f"File created: {backup_file_created}\n"
                error_detail += f"Stderr: {tar_stderr.strip() if tar_stderr and tar_stderr.strip() else '(empty)'}\n"
                error_detail += f"Stdout: {tar_stdout.strip() if tar_stdout and tar_stdout.strip() else '(empty)'}"

                await send_progress("✗ Backup creation failed - file not created")
                await send_progress(f"Command: {tar_cmd}")
                await send_progress(f"Exit successful: {tar_success}")
                await send_progress(f"File created: {backup_file_created}")
                await send_progress(
                    f"Stderr: {tar_stderr.strip() if tar_stderr and tar_stderr.strip() else '(empty)'}"
                )
                await send_progress(
                    f"Stdout: {tar_stdout.strip() if tar_stdout and tar_stdout.strip() else '(empty)'}"
                )

                # Try to check if tar exists and what version
                check_tar_cmd = "which tar && tar --version | head -1"
                check_success, check_out, _ = await self.execute_command(check_tar_cmd)
                if check_success:
                    await send_progress(f"[INFO] Tar location and version: {check_out.strip()}")

                # Check backup directory permissions
                backup_dir_check = f"ls -ld {shlex.quote(backups_dir)}"
                dir_success, dir_out, _ = await self.execute_command(backup_dir_check)
                if dir_success:
                    await send_progress(f"[INFO] Backup directory permissions: {dir_out.strip()}")

                return False, f"Backup creation failed:\n{error_detail}"

            # File was created - backup succeeded even if tar returned non-zero exit code
            # This is common and usually indicates warnings rather than actual failures
            if not tar_success:
                stderr_info = (
                    f" (stderr: {tar_stderr.strip()})" if tar_stderr and tar_stderr.strip() else ""
                )
                await send_progress(
                    f"[WARN] Tar returned non-zero exit code but file was created successfully{stderr_info}"
                )

            await send_progress("✓ Backup archive created successfully")

            # Get backup file size
            size_cmd = f"stat -f%z {shlex.quote(backup_path)} 2>/dev/null || stat -c%s {shlex.quote(backup_path)} 2>/dev/null"
            size_success, size_out, _ = await self.execute_command(size_cmd)

            if size_success and size_out.strip():
                file_size = int(size_out.strip())
                # Convert to human-readable format
                if file_size < 1024:
                    size_str = f"{file_size} bytes"
                elif file_size < 1024 * 1024:
                    size_str = f"{file_size / 1024:.2f} KB"
                elif file_size < 1024 * 1024 * 1024:
                    size_str = f"{file_size / (1024 * 1024):.2f} MB"
                else:
                    size_str = f"{file_size / (1024 * 1024 * 1024):.2f} GB"

                await send_progress(f"✓ Backup file size: {size_str}")

            await send_progress("=" * 60)
            await send_progress("✓ Plugin backup completed successfully!")
            await send_progress(f"Backup saved to: {backup_path}")
            await send_progress("=" * 60)

            self.last_plugin_backup = {
                "path": backup_path,
                "filename": backup_filename,
                "size": file_size,
                "backups_dir": backups_dir,
                "created_at": timestamp,
            }

            return True, f"Plugin backup completed successfully. Saved to: {backup_path}"

        except Exception as e:
            await send_progress(f"Backup error: {str(e)}")
            return False, f"Backup error: {str(e)}"
        finally:
            await self.disconnect()
