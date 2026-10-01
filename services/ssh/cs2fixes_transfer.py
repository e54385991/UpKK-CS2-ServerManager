"""Archive transport stages; the caller owns the SSH lease and installation cleanup."""

# ruff: noqa: F403,F405

from collections.abc import Awaitable, Callable

from .common import *
from .common import _cleanup_local_download_dir

Progress = Callable[..., Awaitable[None]]


async def _download_cs2fixes_panel(
    self: SSHMixinBase, server: Server, temp_dir: str, cs2fixes_url: str, send_progress: Progress
) -> tuple[bool, str] | None:
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
        from services.plugins.download_reuse import cached_download

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

        success_download, error = await cached_download(
            actual_download_url,
            panel_archive_path,
            # Key on the canonical URL so switching GitHub proxies
            # does not invalidate an archive already on disk.
            cache_url=cs2fixes_url,
            scope="framework-cs2fixes",
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
    return None


async def _download_cs2fixes_remote(
    self: SSHMixinBase, server: Server, temp_dir: str, cs2fixes_url: str, send_progress: Progress
) -> tuple[bool, str] | None:
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
        error_detail = f"Download failed. stderr: {stderr[:500] if stderr else 'No error output'}"
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
    return None
