"""CounterStrikeSharpMixin implementation."""

# ruff: noqa: F403,F405

from .common import *
from .common import _cleanup_local_download_dir


class CounterStrikeSharpMixin(SSHMixinBase):
    async def _fetch_counterstrikesharp_release_url(
        self, server: Server, progress_callback=None
    ) -> Tuple[bool, str]:
        """Resolve the Linux runtime asset from the configured network boundary."""

        async def send_progress(message: str) -> None:
            await emit_progress_callback(progress_callback, message)

        api_url = "https://api.github.com/repos/roflmuffin/CounterStrikeSharp/releases/latest"

        if server.use_panel_proxy:
            await send_progress("Resolving CounterStrikeSharp release through the panel...")
            from modules.http_helper import http_helper

            success, payload, error = await http_helper.get(
                api_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30,
                retries=3,
            )
            if success and isinstance(payload, dict):
                assets = payload.get("assets")
                if isinstance(assets, list):
                    for asset in assets:
                        if not isinstance(asset, dict):
                            continue
                        name = str(asset.get("name") or "")
                        url = str(asset.get("browser_download_url") or "")
                        if (
                            "counterstrikesharp-with-runtime-linux" in name.lower()
                            and name.lower().endswith(".zip")
                            and url.startswith(
                                "https://github.com/roflmuffin/CounterStrikeSharp/releases/download/"
                            )
                        ):
                            await send_progress(f"✓ Found latest version: {url}")
                            return True, url

            detail = error or "GitHub API response did not contain the Linux runtime asset"
            return (
                False,
                "Could not determine CounterStrikeSharp version through the panel GitHub API: "
                f"{detail}",
            )

        await send_progress("Resolving CounterStrikeSharp release from the game server...")

        # Direct mode intentionally performs the lookup on the game server. Use
        # fail-on-error and bounded curl timeouts so an unreachable GitHub API is
        # reported as a network problem instead of an empty release URL.
        api_cmd = (
            f"curl -fsSL --connect-timeout 15 --max-time 30 {api_url} | "
            'grep -oP \'"browser_download_url": "\\K[^"]*counterstrikesharp-with-runtime-linux[^"]*\\.zip\' | head -1'
        )
        success, css_url, _ = await self.execute_command(api_cmd, timeout=35)

        if success and css_url.strip():
            return True, css_url.strip()

        await send_progress("⚠ Trying alternative API query...")
        alt_cmd = (
            f"curl -fsSL --connect-timeout 15 --max-time 30 {api_url} | "
            "grep '\"browser_download_url\"' | grep 'with-runtime-linux' | "
            "grep -oP 'https://[^\"]*\\.zip' | head -1"
        )
        success, css_url, _ = await self.execute_command(alt_cmd, timeout=35)
        if success and css_url.strip():
            return True, css_url.strip()

        await send_progress("⚠ Could not fetch from GitHub API, constructing fallback URL...")
        tag_cmd = (
            f"curl -fsSL --connect-timeout 15 --max-time 30 {api_url} | "
            "grep '\"tag_name\"' | grep -oP 'v[0-9.]+' | head -1"
        )
        tag_success, tag, _ = await self.execute_command(tag_cmd, timeout=35)
        if tag_success and tag.strip():
            version = tag.strip().lstrip("v")
            css_url = (
                "https://github.com/roflmuffin/CounterStrikeSharp/releases/download/"
                f"{tag.strip()}/counterstrikesharp-with-runtime-linux-{version}.zip"
            )
            await send_progress(f"Using constructed URL for version {version}")
            return True, css_url

        mode_hint = (
            "The game server could not reach api.github.com in direct mode. "
            "Switch the default proxy mode to panel or configure a reachable GitHub route."
        )
        return False, f"Could not determine CounterStrikeSharp version from GitHub API. {mode_hint}"

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

        async def send_progress(
            message: str,
            kind: str = "status",
            metadata: dict[str, Any] | None = None,
        ):
            """Helper to send progress updates"""
            await emit_progress_callback(progress_callback, message, kind=kind, metadata=metadata)

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
                reconnect_success, reconnect_message = await self.connect(server)
                if not reconnect_success:
                    return (
                        False,
                        f"CounterStrikeSharp reconnect failed after Metamod installation: {reconnect_message}",
                    )
            else:
                await send_progress("✓ Metamod already installed")

            # Get latest CounterStrikeSharp release from GitHub
            await send_progress("Fetching latest CounterStrikeSharp release from GitHub...")

            release_success, css_url = await self._fetch_counterstrikesharp_release_url(
                server, progress_callback
            )
            if not release_success:
                return False, css_url

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

                    # Download to panel server
                    from modules.http_helper import http_helper

                    async def download_event_callback(progress: dict[str, Any]):
                        percent = progress.get("percent")
                        transferred = float(progress.get("bytes_transferred") or 0)
                        total = float(progress.get("total_bytes") or 0)
                        message = (
                            f"Downloading CounterStrikeSharp: {percent:.1f}% "
                            f"({transferred / (1024 * 1024):.1f}/{total / (1024 * 1024):.1f} MB)"
                            if percent is not None
                            else f"Downloading CounterStrikeSharp ({transferred / (1024 * 1024):.1f} MB)"
                        )
                        retry_count = int(progress.get("retry_count") or 0)
                        if retry_count:
                            message = (
                                f"Retrying CounterStrikeSharp download (attempt {retry_count + 1})"
                            )
                        await send_progress(message, metadata={"transfer": progress})

                    async def download_progress_callback(_bytes_downloaded, _total_bytes):
                        return None

                    download_progress_callback.progress_event_callback = download_event_callback

                    success_download, error = await http_helper.download_file(
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

                    async def upload_event_callback(progress: dict[str, Any]):
                        percent = progress.get("percent")
                        transferred = float(progress.get("bytes_transferred") or 0)
                        total = float(progress.get("total_bytes") or 0)
                        message = (
                            f"Uploading CounterStrikeSharp: {percent:.1f}% "
                            f"({transferred / (1024 * 1024):.1f}/{total / (1024 * 1024):.1f} MB)"
                            if percent is not None
                            else f"Uploading CounterStrikeSharp ({transferred / (1024 * 1024):.1f} MB)"
                        )
                        await send_progress(message, metadata={"transfer": progress})

                    async def upload_progress_callback(_bytes_uploaded, _total_bytes):
                        return None

                    upload_progress_callback.progress_event_callback = upload_event_callback

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
