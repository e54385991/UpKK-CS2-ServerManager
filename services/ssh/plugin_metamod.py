"""MetamodMixin implementation."""

# ruff: noqa: F403,F405

from .common import *
from .gameinfo import ensure_remote_gameinfo_search_paths, gameinfo_progress_detail
from .metamod_transfer import _download_metamod_panel, _download_metamod_remote


async def _configure_metamod_gameinfo(
    self: SSHMixinBase, cs2_dir: str, csgo_dir: str, temp_dir: str, send_progress
) -> tuple[bool, str] | None:
    gameinfo_path = f"{cs2_dir}/game/csgo/gameinfo.gi"
    await send_progress("Updating gameinfo.gi...")
    gi_ok, gi_msg, gi_result = await ensure_remote_gameinfo_search_paths(
        self.execute_command,
        gameinfo_path,
        include_metamod=True,
        detect_swiftly_dir=f"{csgo_dir}/addons/swiftlys2",
    )
    if not gi_ok:
        await self.execute_command(f"rm -rf {temp_dir}")
        return False, gi_msg
    if gi_msg == "already configured":
        await send_progress("✓ Metamod already configured in gameinfo.gi")
    else:
        await send_progress("✓ Created backup of gameinfo.gi")
        await send_progress(f"✓ gameinfo.gi updated ({gameinfo_progress_detail(gi_result)})")
        if gi_result is not None and gi_result.has_swiftly:
            await send_progress(
                "✓ Existing SwiftlyS2 SearchPath kept after Metamod so both loaders coexist"
            )
    return None


class MetamodMixin(SSHMixinBase):
    async def install_metamod(self, server: Server, progress_callback=None) -> Tuple[bool, str]:  # noqa: C901 - dependency installation protocol.
        """
        Install Metamod:Source 2.0 for CS2 server

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
            await emit_progress_callback(
                progress_callback,
                message,
                kind=kind,
                metadata=metadata,
            )

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
                download_failure = await _download_metamod_panel(
                    self, server, temp_dir, metamod_url, send_progress
                )
            else:
                download_failure = await _download_metamod_remote(
                    self, server, temp_dir, metamod_url, send_progress
                )
            if download_failure is not None:
                return download_failure

            # Extract Metamod to CS2 csgo directory (tar contains addons/metamod structure)
            csgo_dir = f"{cs2_dir}/game/csgo"
            await send_progress(f"Extracting Metamod to {csgo_dir}...")
            extract_cmd = f"tar -xzf {temp_dir}/metamod.tar.gz -C {csgo_dir}"
            success, stdout, stderr = await self.execute_command(extract_cmd, timeout=60)

            if not success:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, f"Metamod extraction failed: {stderr}"

            await send_progress("✓ Metamod extracted successfully")

            configuration_failure = await _configure_metamod_gameinfo(
                self, cs2_dir, csgo_dir, temp_dir, send_progress
            )
            if configuration_failure is not None:
                return configuration_failure

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

        async def send_progress(
            message: str,
            kind: str = "status",
            metadata: dict[str, Any] | None = None,
        ):
            """Helper to send progress updates"""
            await emit_progress_callback(progress_callback, message, kind=kind, metadata=metadata)

        await send_progress("Updating Metamod:Source to latest version...")
        await send_progress("This will reinstall Metamod with the latest version.")

        # Just reinstall - this will update to the latest version
        return await self.install_metamod(server, progress_callback)
