"""MetamodMixin implementation."""

# ruff: noqa: F403,F405

from .common import *
from .common import _cleanup_local_download_dir


class MetamodMixin(SSHMixinBase):
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

                    actual_download_url = self._apply_github_download_proxy(
                        metamod_url, server.github_proxy
                    )
                    if actual_download_url != metamod_url:
                        await send_progress("Using GitHub proxy for download")

                    success_download, error = await http_helper.download_file(
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
