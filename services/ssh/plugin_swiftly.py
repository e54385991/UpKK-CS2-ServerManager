"""SwiftlyMixin implementation."""

# ruff: noqa: F403,F405

from .common import *
from .gameinfo import ensure_remote_gameinfo_search_paths, gameinfo_progress_detail
from .swiftly_transfer import _download_swiftly_panel, _download_swiftly_remote


async def _ensure_swiftly_unzip(
    self: SSHMixinBase, server: Server, temp_dir: str, send_progress
) -> tuple[bool, str] | None:
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
                    success, stdout, stderr = await self.execute_command(install_cmd, timeout=120)

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
    return None


async def _extract_and_verify_swiftly(
    self: SSHMixinBase, cs2_dir: str, temp_dir: str, send_progress
) -> tuple[bool, str]:
    csgo_dir = f"{cs2_dir}/game/csgo"
    extract_dir = f"{temp_dir}/extracted"
    await send_progress("Extracting SwiftlyS2...")
    extract_cmd = f"mkdir -p {extract_dir} && unzip -o {temp_dir}/swiftly.zip -d {extract_dir}"
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

    # Official archives ship addons/swiftlys2 (the Loader). Copy only that
    # tree so a leftover addons/metamod in an older zip cannot overwrite a
    # panel-installed Metamod.
    swiftly_src = f"{addons_path.rstrip('/')}/swiftlys2"
    check_src = f"test -d {shlex.quote(swiftly_src)} && echo 'found'"
    src_ok, src_out, _ = await self.execute_command(check_src)
    if not src_ok or "found" not in src_out:
        await self.execute_command(f"rm -rf {temp_dir}")
        return False, "SwiftlyS2 extraction failed: addons/swiftlys2 not found in archive"

    await send_progress(f"Copying SwiftlyS2 Loader to {csgo_dir}/addons/swiftlys2...")
    copy_cmd = (
        f"mkdir -p {shlex.quote(csgo_dir + '/addons')} && "
        f"cp -rf {shlex.quote(swiftly_src)} {shlex.quote(csgo_dir + '/addons/')}"
    )
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
        gameinfo_path = f"{csgo_dir}/gameinfo.gi"
        await send_progress("Updating gameinfo.gi for the SwiftlyS2 Loader...")
        gi_ok, gi_msg, gi_result = await ensure_remote_gameinfo_search_paths(
            self.execute_command,
            gameinfo_path,
            include_swiftly=True,
            detect_metamod_dir=f"{csgo_dir}/addons/metamod",
        )
        if not gi_ok:
            return False, gi_msg
        if gi_msg == "already configured":
            await send_progress("✓ SwiftlyS2 already configured in gameinfo.gi")
        else:
            await send_progress(f"✓ gameinfo.gi updated ({gameinfo_progress_detail(gi_result)})")
            if gi_result is not None and gi_result.has_metamod:
                await send_progress(
                    "✓ Existing Metamod SearchPath kept first so both loaders coexist"
                )
        await send_progress("=" * 60)
        await send_progress("✓ SwiftlyS2 installed successfully!")
        await send_progress("=" * 60)
        await send_progress("NOTE: You need to restart your server for changes to take effect.")
        await send_progress(
            "SwiftlyS2 uses its own Loader (Game csgo/addons/swiftlys2). "
            "It does not require Metamod."
        )
        await send_progress(
            "After restart, use 'sw' in console to verify SwiftlyS2. "
            "If Metamod is also installed, use 'meta list' to verify it."
        )
        return True, "SwiftlyS2 installed successfully"
    else:
        return False, "SwiftlyS2 installation verification failed"


class SwiftlyMixin(SSHMixinBase):
    async def install_swiftly(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Install SwiftlyS2 framework for CS2 server

        SwiftlyS2 is a C#/Lua plugin framework with its own Loader
        (``Game csgo/addons/swiftlys2``). It does not need Metamod, but keeps a
        previously installed Metamod SearchPath and lists Metamod first.

        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if inspect.iscoroutinefunction(progress_callback):
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
                download_failure = await _download_swiftly_panel(
                    self, server, temp_dir, swiftly_url, send_progress
                )
            else:
                download_failure = await _download_swiftly_remote(
                    self, server, temp_dir, swiftly_url, send_progress
                )
            if download_failure is not None:
                return download_failure

            # Check if unzip is available and try to install if missing
            unzip_failure = await _ensure_swiftly_unzip(self, server, temp_dir, send_progress)
            if unzip_failure is not None:
                return unzip_failure

            # Extract SwiftlyS2 to CS2 directory
            # The zip contains a version-named top-level directory (e.g. swiftlys2-linux-v1.2.0-with-runtimes/addons/...)
            # We need to strip that top-level directory and copy the contents into csgo/
            return await _extract_and_verify_swiftly(self, cs2_dir, temp_dir, send_progress)

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
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        await send_progress("Updating SwiftlyS2 to latest version...")
        await send_progress("This will reinstall SwiftlyS2 with the latest version.")

        # Just reinstall - this will update to the latest version
        return await self.install_swiftly(server, progress_callback)
