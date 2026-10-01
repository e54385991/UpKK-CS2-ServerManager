"""CS2FixesMixin implementation."""

# ruff: noqa: F403,F405

from .common import *
from .cs2fixes_transfer import _download_cs2fixes_panel, _download_cs2fixes_remote


async def _ensure_cs2fixes_metamod(
    self: SSHMixinBase, server: Server, cs2_dir: str, progress_callback, send_progress
) -> tuple[bool, str] | None:
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
    return None


async def _extract_and_verify_cs2fixes(
    self: SSHMixinBase, server: Server, cs2_dir: str, temp_dir: str, send_progress
) -> tuple[bool, str]:
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
        await send_progress("NOTE: You need to restart your server for changes to take effect.")
        await send_progress("Use 'meta list' command to verify CS2Fixes is loaded.")
        return True, "CS2Fixes installed successfully"
    else:
        return False, "CS2Fixes installation verification failed"


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
                if inspect.iscoroutinefunction(progress_callback):
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
            framework_failure = await _ensure_cs2fixes_metamod(
                self, server, cs2_dir, progress_callback, send_progress
            )
            if framework_failure is not None:
                return framework_failure

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
                download_failure = await _download_cs2fixes_panel(
                    self, server, temp_dir, cs2fixes_url, send_progress
                )
            else:
                download_failure = await _download_cs2fixes_remote(
                    self, server, temp_dir, cs2fixes_url, send_progress
                )
            if download_failure is not None:
                return download_failure

            # Extract CS2Fixes directly to CS2 directory
            return await _extract_and_verify_cs2fixes(
                self, server, cs2_dir, temp_dir, send_progress
            )

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
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        await send_progress("Updating CS2Fixes to latest version...")
        await send_progress("This will reinstall CS2Fixes with the latest version.")

        # Just reinstall - this will update to the latest version
        return await self.install_cs2fixes(server, progress_callback)
