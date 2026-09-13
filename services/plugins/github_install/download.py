"""Download a GitHub plugin archive onto the managed server."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import tempfile
import uuid

from anyio import to_thread

from modules import GitHubPluginInstallResponse
from services.plugins.github_install.context import GithubInstallContext, host

logger = logging.getLogger(__name__)


async def download_plugin_archive(  # noqa: C901
    ctx: GithubInstallContext,
) -> GitHubPluginInstallResponse | tuple[str, str, bool]:
    request = ctx.request
    server = ctx.server
    current_user = ctx.current_user
    ssh_manager = ctx.ssh_manager
    remote_temp_dir = ctx.remote_temp_dir
    progress = ctx.progress
    notify_install_result = ctx.notify_install_result

    # Detect archive type (support zip, tar.gz, tgz, tar, 7z)
    url_lower = request.download_url.lower()
    if url_lower.endswith(".zip"):
        archive_type = "zip"
        archive_filename = "plugin.zip"
    elif url_lower.endswith(".tar.gz") or url_lower.endswith(".tgz"):
        archive_type = "tar.gz"
        archive_filename = "plugin.tar.gz"
    elif url_lower.endswith(".tar"):
        archive_type = "tar"
        archive_filename = "plugin.tar"
    elif url_lower.endswith(".7z"):
        archive_type = "7z"
        archive_filename = "plugin.7z"
    else:
        archive_type = "zip"  # Default assumption
        archive_filename = "plugin.zip"

    # Approved plans always download through the panel's strict GitHub
    # redirect allowlist. This bypasses server proxies and remote curl so a
    # release cannot redirect the managed host to an arbitrary destination.
    secure_plan_download = bool(request.expected_archive_sha256)
    if secure_plan_download:
        from services.plugins.download_reuse import cached_release_asset

        await progress("Downloading approved release through the secure GitHub gateway...")
        local_archive_path, local_digest, _local_size = await cached_release_asset(
            request.download_url,
            scope=host._archive_cache_scope(request),
            version=request.release_tag,
            expected_sha256=request.expected_archive_sha256,
        )
        try:
            if (
                request.expected_archive_sha256
                and local_digest.casefold() != request.expected_archive_sha256.casefold()
            ):
                error_msg = "Release archive digest changed after approval"
                await progress(error_msg, "error")
                await notify_install_result(False, error_msg)
                return GitHubPluginInstallResponse(success=False, message=error_msg)
            await ssh_manager.execute_command(
                f"rm -rf -- {shlex.quote(remote_temp_dir)} && "
                f"mkdir -p -- {shlex.quote(remote_temp_dir)}"
            )
            archive_file = f"{remote_temp_dir}/{archive_filename}"
            uploaded, upload_error = await ssh_manager.upload_file_with_progress(
                local_archive_path,
                archive_file,
                server,
            )
            if not uploaded:
                error_msg = f"Failed to upload approved release: {upload_error}"
                await progress(error_msg, "error")
                await notify_install_result(False, error_msg)
                return GitHubPluginInstallResponse(success=False, message=error_msg)
        finally:
            try:
                os.unlink(local_archive_path)
            except OSError:
                pass
    # Legacy non-plan callers retain the existing optional panel proxy path.
    elif server.use_panel_proxy:
        # Panel Proxy Mode: Download to panel server first, then SFTP upload
        await progress("Using panel server proxy mode (github_proxy setting ignored)...")

        # Create UID-isolated temp directory on panel server
        panel_temp_dir = os.path.join(tempfile.gettempdir(), f"cs2_panel_proxy_{current_user.id}")
        os.makedirs(panel_temp_dir, exist_ok=True)

        # Create unique subdirectory for this download
        download_id = str(uuid.uuid4())
        download_dir = os.path.join(panel_temp_dir, download_id)
        os.makedirs(download_dir, exist_ok=True)

        panel_archive_path = os.path.join(download_dir, archive_filename)

        try:
            # Download to panel server
            await progress(f"Downloading {archive_type} archive to panel server...")
            logger.info(
                f"Panel proxy: Downloading from {request.download_url} to {panel_archive_path}"
            )

            from services.plugins.download_reuse import cached_download

            # Progress tracking for download
            last_progress_percent = 0

            async def download_progress(bytes_downloaded, total_bytes):
                nonlocal last_progress_percent
                if total_bytes > 0:
                    percent = int((bytes_downloaded / total_bytes) * 100)
                    # Only update at configured interval
                    if (
                        percent >= last_progress_percent + host.PROGRESS_UPDATE_INTERVAL
                        or percent == 100
                    ):
                        last_progress_percent = percent
                        size_mb = bytes_downloaded / (1024 * 1024)
                        total_mb = total_bytes / (1024 * 1024)
                        await progress(
                            f"Download progress: {percent}% ({size_mb:.1f}/{total_mb:.1f} MB)"
                        )

            success, error = await cached_download(
                request.download_url,
                panel_archive_path,
                scope=host._archive_cache_scope(request),
                version=request.release_tag,
                timeout=600,
                progress_callback=download_progress,
            )

            if not success:
                await progress(f"Failed to download to panel server: {error}", "error")
                await notify_install_result(False, f"Failed to download to panel server: {error}")
                return GitHubPluginInstallResponse(
                    success=False, message=f"Failed to download to panel server: {error}"
                )

            # Verify download
            if not os.path.exists(panel_archive_path):
                await progress("Downloaded file not found", "error")
                await notify_install_result(False, "Downloaded file not found")
                return GitHubPluginInstallResponse(
                    success=False, message="Downloaded file not found"
                )

            file_size = os.path.getsize(panel_archive_path)
            if file_size < 1000:
                await progress("Downloaded file is too small or empty", "error")
                await notify_install_result(False, "Downloaded file is too small or empty")
                return GitHubPluginInstallResponse(
                    success=False, message="Downloaded file is too small or empty"
                )

            # Format file size for display
            if file_size >= 1024 * 1024:
                size_str = f"{file_size / (1024 * 1024):.2f} MB"
            elif file_size >= 1024:
                size_str = f"{file_size / 1024:.2f} KB"
            else:
                size_str = f"{file_size} B"

            await progress(f"Download complete ({size_str}), uploading to server via SFTP...")

            # Upload to remote server via SFTP
            await ssh_manager.execute_command(
                f"rm -rf -- {shlex.quote(remote_temp_dir)} && "
                f"mkdir -p -- {shlex.quote(remote_temp_dir)}"
            )
            remote_archive_path = f"{remote_temp_dir}/{archive_filename}"

            # Progress tracking for upload
            last_upload_percent = 0

            async def upload_progress(bytes_uploaded, total_bytes):
                nonlocal last_upload_percent
                if total_bytes > 0:
                    percent = int((bytes_uploaded / total_bytes) * 100)
                    # Only update at configured interval
                    if (
                        percent >= last_upload_percent + host.PROGRESS_UPDATE_INTERVAL
                        or percent == 100
                    ):
                        last_upload_percent = percent
                        size_mb = bytes_uploaded / (1024 * 1024)
                        total_mb = total_bytes / (1024 * 1024)
                        await progress(
                            f"Upload progress: {percent}% ({size_mb:.1f}/{total_mb:.1f} MB)"
                        )

            success, error = await ssh_manager.upload_file_with_progress(
                panel_archive_path,
                remote_archive_path,
                server,
                progress_callback=upload_progress,
            )

            if not success:
                await progress(f"Failed to upload to server: {error}", "error")
                await notify_install_result(False, f"Failed to upload to server: {error}")
                return GitHubPluginInstallResponse(
                    success=False, message=f"Failed to upload to server: {error}"
                )

            await progress("Upload complete, proceeding with extraction...")

            # Set archive_file for extraction phase
            archive_file = remote_archive_path

        finally:
            # Clean up panel temp directory
            try:
                if os.path.exists(download_dir):
                    await to_thread.run_sync(shutil.rmtree, download_dir)
                    logger.info(f"Cleaned up panel temp directory: {download_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up panel temp directory {download_dir}: {e}")
    else:
        # Original Mode: Download directly on remote server
        # Create temp directory
        await ssh_manager.execute_command(
            f"rm -rf -- {shlex.quote(remote_temp_dir)} && "
            f"mkdir -p -- {shlex.quote(remote_temp_dir)}"
        )
        archive_file = f"{remote_temp_dir}/{archive_filename}"

        # Download archive with progress (use GitHub proxy if configured)
        await progress(f"Downloading plugin archive ({archive_type})...")
        logger.info(f"Downloading plugin from {request.download_url}")

        # Apply GitHub proxy if configured
        actual_download_url = request.download_url
        if server.github_proxy and server.github_proxy.strip():
            proxy_base = server.github_proxy.strip().rstrip("/")
            actual_download_url = f"{proxy_base}/{request.download_url}"
            logger.info(f"Using GitHub proxy: {proxy_base}")

        # Use curl with progress output
        download_cmd = f"curl -fL --progress-bar -o {archive_file} '{actual_download_url}' 2>&1"
        success, download_output, stderr = await ssh_manager.execute_command(
            download_cmd, timeout=300
        )

        if not success:
            await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
            await progress(f"Failed to download plugin: {stderr}", "error")
            await notify_install_result(False, f"Failed to download plugin: {stderr}")
            return GitHubPluginInstallResponse(
                success=False, message=f"Failed to download plugin: {stderr}"
            )

    # Continue with common extraction logic. All modes use the same
    # operation-scoped remote staging directory.
    if request.expected_archive_sha256:
        await progress("Verifying immutable release archive digest...")
        digest_cmd = f"sha256sum -- {shlex.quote(archive_file)} | awk '{{print $1}}'"
        digest_ok, digest_output, _ = await ssh_manager.execute_command(digest_cmd)
        actual_digest = digest_output.strip().casefold()
        if not digest_ok or actual_digest != request.expected_archive_sha256.casefold():
            await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
            error_msg = "Release archive digest changed after approval"
            await progress(error_msg, "error")
            await notify_install_result(False, error_msg)
            return GitHubPluginInstallResponse(success=False, message=error_msg)

    # Verify download and get file size (only needed for non-panel-proxy mode)
    if not server.use_panel_proxy and not secure_plan_download:
        size_cmd = f"stat -c%s {archive_file} 2>/dev/null || stat -f%z {archive_file} 2>/dev/null"
        success, size_output, _ = await ssh_manager.execute_command(size_cmd)

        if not success or not size_output.strip():
            await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
            await progress("Downloaded file is invalid", "error")
            await notify_install_result(False, "Downloaded file is invalid")
            return GitHubPluginInstallResponse(success=False, message="Downloaded file is invalid")

        file_size = int(size_output.strip())
        if file_size < 1000:
            await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
            await progress("Downloaded file is too small or empty", "error")
            await notify_install_result(False, "Downloaded file is too small or empty")
            return GitHubPluginInstallResponse(
                success=False, message="Downloaded file is too small or empty"
            )

        # Format file size for display
        if file_size >= 1024 * 1024:
            size_str = f"{file_size / (1024 * 1024):.2f} MB"
        elif file_size >= 1024:
            size_str = f"{file_size / 1024:.2f} KB"
        else:
            size_str = f"{file_size} B"

        await progress(f"Download complete ({size_str})")

    return archive_file, archive_type, secure_plan_download
