"""CS2FixesMixin implementation."""

# ruff: noqa: F403,F405

from .common import *
from .common import _cleanup_local_download_dir


class CS2FixesMixin(SSHMixinBase):
    async def install_cs2fixes(self, server: Server, progress_callback=None) -> Tuple[bool, str]:  # noqa: C901 - plugin installation protocol.
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

                    # Apply GitHub proxy to download URL if configured
                    actual_download_url = cs2fixes_url
                    if server.github_proxy and server.github_proxy.strip():
                        proxy_base = server.github_proxy.strip().rstrip("/")
                        actual_download_url = f"{proxy_base}/{cs2fixes_url}"
                        await send_progress("Using GitHub proxy for download")

                    success_download, error = await http_helper.download_file(
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
