"""SwiftlyMixin implementation."""

# ruff: noqa: F403,F405

from .common import *
from .common import _cleanup_local_download_dir


class SwiftlyMixin(SSHMixinBase):
    async def install_swiftly(self, server: Server, progress_callback=None) -> Tuple[bool, str]:  # noqa: C901 - plugin installation protocol.
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
                            install_cmd = f"echo '{server.sudo_password}' | sudo -S apt-get update && echo '{server.sudo_password}' | sudo -S apt-get install -y unzip"
                            success, stdout, stderr = await self.execute_command(
                                install_cmd, timeout=120
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
