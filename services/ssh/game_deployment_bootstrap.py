"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("services.ssh.game_deployment")


async def _download_steamcmd_panel(
    self: SSHMixinBase, server: Server, steamcmd_dir: str, send_progress
) -> None:
    await send_progress("Using panel server proxy mode for SteamCMD download...")

    steamcmd_url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
    steamcmd_local_path = None

    try:
        # Create temp directory on panel server
        panel_temp_dir = host.os.path.join(
            host.tempfile.gettempdir(), f"cs2_panel_proxy_steamcmd_{server.user_id}"
        )
        host.os.makedirs(panel_temp_dir, exist_ok=True)

        # Create unique subdirectory
        download_id = str(host.uuid.uuid4())
        download_dir = host.os.path.join(panel_temp_dir, download_id)
        host.os.makedirs(download_dir, exist_ok=True)

        steamcmd_local_path = host.os.path.join(download_dir, "steamcmd_linux.tar.gz")

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
        file_size = host.os.path.getsize(steamcmd_local_path)
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
            await host._cleanup_local_download_dir(download_dir, panel_temp_dir)


async def _download_steamcmd_remote(
    self: SSHMixinBase, steamcmd_dir: str, send_progress
) -> tuple[bool, str] | None:
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
    return None


async def _bootstrap_steamcmd(
    self: SSHMixinBase, server: Server, send_progress
) -> tuple[bool, str]:
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
        await host._download_steamcmd_panel(self, server, steamcmd_dir, send_progress)
    else:
        # Original Mode: Download directly on remote server
        download_failure = await host._download_steamcmd_remote(self, steamcmd_dir, send_progress)
        if download_failure is not None:
            return download_failure

    # Extract SteamCMD
    await send_progress("Extracting SteamCMD...")
    success, stdout, stderr = await self.execute_command(
        f"tar -xzf {steamcmd_dir}/steamcmd_linux.tar.gz -C {steamcmd_dir}", timeout=120
    )
    if not success:
        return False, f"SteamCMD extraction failed: {stderr}"
    await send_progress("✓ SteamCMD extracted successfully")
    return True, steamcmd_dir
