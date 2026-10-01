"""Archive transport stages; the caller owns the SSH lease and installation cleanup."""

# ruff: noqa: F403,F405

from collections.abc import Awaitable, Callable

from .common import *
from .common import _cleanup_local_download_dir

Progress = Callable[..., Awaitable[None]]


async def _download_metamod_panel(
    self: SSHMixinBase, server: Server, temp_dir: str, metamod_url: str, send_progress: Progress
) -> tuple[bool, str] | None:
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

        # Download to panel server, reusing the local archive cache
        from services.plugins.download_reuse import cached_download

        async def download_event_callback(progress: dict[str, Any]):
            percent = progress.get("percent")
            transferred = float(progress.get("bytes_transferred") or 0)
            total = float(progress.get("total_bytes") or 0)
            if percent is None:
                message = f"Downloading Metamod ({transferred / (1024 * 1024):.1f} MB)"
            else:
                message = (
                    f"Downloading Metamod: {percent:.1f}% "
                    f"({transferred / (1024 * 1024):.1f}/{total / (1024 * 1024):.1f} MB)"
                )
            retry_count = int(progress.get("retry_count") or 0)
            if retry_count:
                message = f"Retrying Metamod download (attempt {retry_count + 1})"
            await send_progress(message, metadata={"transfer": progress})

        async def download_progress_callback(_bytes_downloaded, _total_bytes):
            return None

        download_progress_callback.progress_event_callback = download_event_callback

        actual_download_url = self._apply_github_download_proxy(metamod_url, server.github_proxy)
        if actual_download_url != metamod_url:
            await send_progress("Using GitHub proxy for download")

        success_download, error = await cached_download(
            actual_download_url,
            panel_archive_path,
            # Key on the canonical URL so switching GitHub proxies
            # does not invalidate an archive already on disk.
            cache_url=metamod_url,
            scope="framework-metamod",
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

        async def upload_event_callback(progress: dict[str, Any]):
            percent = progress.get("percent")
            transferred = float(progress.get("bytes_transferred") or 0)
            total = float(progress.get("total_bytes") or 0)
            message = (
                f"Uploading Metamod: {percent:.1f}% "
                f"({transferred / (1024 * 1024):.1f}/{total / (1024 * 1024):.1f} MB)"
                if percent is not None
                else f"Uploading Metamod ({transferred / (1024 * 1024):.1f} MB)"
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
            raise Exception(f"Failed to upload Metamod: {error}")

        await send_progress("✓ Metamod uploaded successfully")

    finally:
        # Clean up panel temp directory
        if panel_archive_path:
            await _cleanup_local_download_dir(download_dir, panel_temp_dir)
    return None


async def _download_metamod_remote(
    self: SSHMixinBase, server: Server, temp_dir: str, metamod_url: str, send_progress: Progress
) -> tuple[bool, str] | None:
    actual_download_url = self._apply_github_download_proxy(metamod_url, server.github_proxy)
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
        error_detail = f"Download failed. stderr: {stderr[:500] if stderr else 'No error output'}"
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
    return None
