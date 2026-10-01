"""CounterStrikeSharpMixin implementation."""

# ruff: noqa: F403,F405

from services.plugins.counterstrikesharp_core import apply_counterstrikesharp_core_defaults

from .common import *
from .counterstrikesharp_transfer import (
    _download_counterstrikesharp_panel,
    _download_counterstrikesharp_remote,
)


async def _ensure_counterstrikesharp_unzip(
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
            # Try to install without sudo first
            install_cmd = "apt-get update && apt-get install -y unzip"
            success, stdout, stderr = await self.execute_command(install_cmd, timeout=120)

            if not success:
                # Try with sudo if available
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


async def _extract_and_verify_counterstrikesharp(
    self: SSHMixinBase, server: Server, cs2_dir: str, temp_dir: str, send_progress
) -> tuple[bool, str]:
    await send_progress("Extracting CounterStrikeSharp...")
    extract_cmd = f"unzip -o {temp_dir}/counterstrikesharp.zip -d {cs2_dir}/game/csgo/"
    success, stdout, stderr = await self.execute_command(extract_cmd, timeout=120)

    if not success:
        await self.execute_command(f"rm -rf {temp_dir}")
        extraction_error = stderr or stdout or "unzip returned a non-zero status"
        return False, f"CounterStrikeSharp extraction failed: {extraction_error}"

    # Check if extraction actually succeeded by checking the directory
    verify_extract = f"test -d {cs2_dir}/game/csgo/addons/counterstrikesharp && echo 'extracted'"
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
        # Community plugins need the guidelines gate off, and a fresh
        # install has no core.json until the first launch writes one.
        await apply_counterstrikesharp_core_defaults(
            self.execute_command,
            f"{cs2_dir}/game/csgo",
            report=send_progress,
        )
        await send_progress("=" * 60)
        await send_progress("✓ CounterStrikeSharp installed successfully!")
        await send_progress("=" * 60)
        await send_progress("NOTE: You need to restart your server for changes to take effect.")
        await send_progress("After restart, use 'meta list' and 'css_plugins list' to verify.")
        return True, "CounterStrikeSharp installed successfully"
    else:
        return False, "CounterStrikeSharp installation verification failed"


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
                download_failure = await _download_counterstrikesharp_panel(
                    self, server, temp_dir, css_url, send_progress
                )
            else:
                download_failure = await _download_counterstrikesharp_remote(
                    self, server, temp_dir, css_url, send_progress
                )
            if download_failure is not None:
                return download_failure

            # Check if unzip is available and try to install if missing
            unzip_failure = await _ensure_counterstrikesharp_unzip(
                self, server, temp_dir, send_progress
            )
            if unzip_failure is not None:
                return unzip_failure

            # Extract CounterStrikeSharp to CS2 directory
            # The zip contains an 'addons' folder that should merge with the existing addons
            return await _extract_and_verify_counterstrikesharp(
                self, server, cs2_dir, temp_dir, send_progress
            )

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
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        await send_progress("Updating CounterStrikeSharp to latest version...")
        await send_progress("This will reinstall CounterStrikeSharp with the latest version.")

        # Just reinstall - this will update to the latest version
        return await self.install_counterstrikesharp(server, progress_callback)
