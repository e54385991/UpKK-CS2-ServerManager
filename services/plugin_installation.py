"""GitHub plugin installation use case shared by API and schedulers."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import tempfile
import uuid
from typing import Any, Optional, Protocol

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from modules import (
    GitHubPluginInstallRequest,
    GitHubPluginInstallResponse,
    Server,
    User,
)
from services.deployment_progress import send_deployment_update
from services.discord_notification_service import (
    EVENT_PLUGIN_UPDATE,
    discord_notification_service,
)
from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)
PROGRESS_UPDATE_INTERVAL = 10


class PluginDownloadHTTP(Protocol):
    """Minimal outbound HTTP surface used by panel-proxy installations."""

    async def download_file(
        self,
        url: str,
        destination: str,
        **kwargs: Any,
    ) -> tuple[bool, Optional[str]]: ...


def _build_plugin_copy_command(
    source_dir: str,
    target_dir: str,
    exclude_patterns: list[str],
    *,
    use_rsync: bool,
) -> str:
    """Build a plugin copy command while always refreshing gamedata files.

    Upgrade exclusions intentionally preserve user configuration files by
    extension.  Framework gamedata often uses those same extensions (for
    example CounterStrikeSharp's ``gamedata.json`` and CS2Fixes'
    ``cs2fixes.jsonc``), so a final, unconditional copy of every file below a
    ``gamedata`` directory is required after the filtered copy.
    """
    safe_source = shlex.quote(source_dir)
    safe_target = shlex.quote(target_dir)

    if use_rsync:
        exclusions = "".join(f" --exclude={shlex.quote(pattern)}" for pattern in exclude_patterns)
        primary_copy = f"rsync -av{exclusions} {safe_source}/ {safe_target}/"
    elif exclude_patterns:
        exclusions = " ".join(f"--exclude={shlex.quote(pattern)}" for pattern in exclude_patterns)
        primary_copy = f"cd {safe_source} && tar {exclusions} -cf - . | tar -xf - -C {safe_target}"
    else:
        primary_copy = f"cp -r {safe_source}/. {safe_target}/"

    # GNU find's batched -exec form propagates a non-zero copy status.  Rebuild
    # each archive-relative parent path below the destination, then force the
    # file copy so gamedata is immune to every upgrade/manual exclusion rule.
    copy_script = (
        'target="$1"; shift; '
        "for source do "
        'relative=${source#./}; destination="$target/$relative"; '
        'parent=${destination%/*}; mkdir -p "$parent" '
        "&& cp -a --no-dereference --remove-destination -- "
        '"$source" "$destination" || exit 1; '
        "done"
    )
    gamedata_copy = (
        f"cd {safe_source} && "
        "find . -path '*/gamedata/*' -type f "
        f"-exec sh -c {shlex.quote(copy_script)} sh {safe_target} {{}} +"
    )
    return f"{primary_copy} && {gamedata_copy}"


async def get_server_for_user(
    db: AsyncSession,
    server_id: int,
    user: User,
) -> Server:
    server = (
        await Server.get_by_id(db, server_id)
        if user.is_admin
        else await Server.get_by_id_and_user(db, server_id, user.id)
    )
    if server is None:
        raise LookupError("Server not found")
    snapshot = Server.model_validate(server, from_attributes=True)
    await db.commit()
    return snapshot


async def install_github_plugin(
    server_id: int,
    request: GitHubPluginInstallRequest,
    db: AsyncSession,
    current_user: User,
    *,
    ssh_manager: SSHManager | None = None,
    http_resource: PluginDownloadHTTP | None = None,
) -> GitHubPluginInstallResponse:
    """
    Install a plugin from a GitHub release asset with WebSocket progress updates.

    This endpoint:
    1. Downloads the archive from GitHub
    2. Analyzes the archive structure (must contain addons/ directory)
    3. Extracts files to the CS2 game directory
    4. Optionally excludes specified directories (for updates)

    Args:
        server_id: Server ID
        request: Installation request with download URL and options

    Returns:
        Installation result
    """

    user_id = current_user.id
    server = await get_server_for_user(db, server_id, current_user)
    if ssh_manager is None:
        # Compatibility path for schedulers and direct Python callers.
        ssh_manager = SSHManager()
    if http_resource is None:
        # Compatibility path for schedulers and direct Python callers.
        from modules.http_helper import http_helper

        http_resource = http_helper

    async def progress(msg: str, msg_type: str = "status"):
        """Send progress update via WebSocket"""
        await send_deployment_update(server_id, msg_type, msg)

    async def notify_install_result(
        success: bool,
        message: str,
        installed_files: Optional[int] = None,
    ) -> None:
        if request.suppress_notification:
            return
        details = {
            "Download URL": request.download_url,
        }
        if installed_files is not None:
            details["Installed Files"] = installed_files

        discord_notification_service.queue_notify(
            server,
            EVENT_PLUGIN_UPDATE,
            "install_github_plugin",
            success,
            message,
            title=f"GitHub plugin install {'completed' if success else 'failed'}",
            details=details,
        )

    async def record_installation() -> None:
        if not request.record_installation or not request.repo_url:
            return
        from services.plugin_auto_update_service import (
            canonical_repo_url,
            derive_asset_glob,
            upsert_managed_plugin,
        )

        repo_url = canonical_repo_url(request.repo_url)
        await upsert_managed_plugin(
            server_id=server.id,
            source_type="github",
            source_key=repo_url.lower(),
            display_name=request.display_name or repo_url.rsplit("/", 1)[-1],
            repo_url=repo_url,
            installed_release_id=request.release_id,
            installed_version=request.release_tag or "unknown",
            asset_glob=request.asset_glob
            or derive_asset_glob(request.asset_name, request.release_tag),
            custom_install_path=request.custom_install_path,
            exclude_dirs=request.exclude_dirs,
            exclude_files=request.exclude_files,
        )

    success, msg = await ssh_manager.connect(server)

    if not success:
        await ssh_manager.disconnect()
        await progress(f"SSH connection failed: {msg}", "error")
        await notify_install_result(False, f"SSH connection failed: {msg}")
        return GitHubPluginInstallResponse(success=False, message=f"SSH connection failed: {msg}")

    try:
        await progress("Connected to server, starting plugin installation...")

        # Verify CS2 is installed
        cs2_dir = f"{server.game_directory}/cs2"
        csgo_dir = f"{cs2_dir}/game/csgo"

        await progress("Checking CS2 server installation...")
        check_cmd = f"test -d {csgo_dir} && echo 'exists'"
        success, check_output, _ = await ssh_manager.execute_command(check_cmd)

        if not success or "exists" not in check_output:
            await progress("CS2 server not found. Please deploy the server first.", "error")
            await notify_install_result(
                False, "CS2 server not found. Please deploy the server first."
            )
            return GitHubPluginInstallResponse(
                success=False, message="CS2 server not found. Please deploy the server first."
            )

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

        # Check if we should use panel proxy mode (server-level setting)
        if server.use_panel_proxy:
            # Panel Proxy Mode: Download to panel server first, then SFTP upload
            await progress("Using panel server proxy mode (github_proxy setting ignored)...")

            # Create UID-isolated temp directory on panel server
            panel_temp_dir = os.path.join(tempfile.gettempdir(), f"cs2_panel_proxy_{user_id}")
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

                # Progress tracking for download
                last_progress_percent = 0

                async def download_progress(bytes_downloaded, total_bytes):
                    nonlocal last_progress_percent
                    if total_bytes > 0:
                        percent = int((bytes_downloaded / total_bytes) * 100)
                        # Only update at configured interval
                        if (
                            percent >= last_progress_percent + PROGRESS_UPDATE_INTERVAL
                            or percent == 100
                        ):
                            last_progress_percent = percent
                            size_mb = bytes_downloaded / (1024 * 1024)
                            total_mb = total_bytes / (1024 * 1024)
                            await progress(
                                f"Download progress: {percent}% ({size_mb:.1f}/{total_mb:.1f} MB)"
                            )

                success, error = await http_resource.download_file(
                    request.download_url,
                    panel_archive_path,
                    timeout=600,
                    progress_callback=download_progress,
                )

                if not success:
                    await progress(f"Failed to download to panel server: {error}", "error")
                    await notify_install_result(
                        False, f"Failed to download to panel server: {error}"
                    )
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
                remote_temp_dir = f"/tmp/github_plugin_{server_id}"
                await ssh_manager.execute_command(
                    f"rm -rf {remote_temp_dir} && mkdir -p {remote_temp_dir}"
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
                            percent >= last_upload_percent + PROGRESS_UPDATE_INTERVAL
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
            temp_dir = f"/tmp/github_plugin_{server_id}"
            await ssh_manager.execute_command(f"rm -rf {temp_dir} && mkdir -p {temp_dir}")
            archive_file = f"{temp_dir}/{archive_filename}"

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
                await ssh_manager.execute_command(f"rm -rf {temp_dir}")
                await progress(f"Failed to download plugin: {stderr}", "error")
                await notify_install_result(False, f"Failed to download plugin: {stderr}")
                return GitHubPluginInstallResponse(
                    success=False, message=f"Failed to download plugin: {stderr}"
                )

        # Continue with common extraction logic
        # Get remote temp directory based on mode
        if server.use_panel_proxy:
            remote_temp_dir = f"/tmp/github_plugin_{server_id}"
        else:
            remote_temp_dir = temp_dir

        # Verify download and get file size (only needed for non-panel-proxy mode)
        if not server.use_panel_proxy:
            size_cmd = (
                f"stat -c%s {archive_file} 2>/dev/null || stat -f%z {archive_file} 2>/dev/null"
            )
            success, size_output, _ = await ssh_manager.execute_command(size_cmd)

            if not success or not size_output.strip():
                await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
                await progress("Downloaded file is invalid", "error")
                await notify_install_result(False, "Downloaded file is invalid")
                return GitHubPluginInstallResponse(
                    success=False, message="Downloaded file is invalid"
                )

            file_size = int(size_output.strip())
            if file_size < 1000:
                await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
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

        # Create extraction directory
        extract_dir = f"{remote_temp_dir}/extracted"
        await ssh_manager.execute_command(f"mkdir -p {extract_dir}")

        # Extract archive (support zip, tar.gz, tar, 7z)
        await progress(f"Extracting {archive_type} archive...")
        if archive_type == "zip":
            extract_cmd = f"unzip -o {archive_file} -d {extract_dir}"
        elif archive_type == "7z":
            # Check if 7z is available
            check_7z = "command -v 7z || command -v 7za"
            success, seven_zip_path, _ = await ssh_manager.execute_command(check_7z)
            if not seven_zip_path.strip():
                extract_cmd = f"7za x -y -o{extract_dir} {archive_file} 2>/dev/null || 7zr x -y -o{extract_dir} {archive_file}"
            else:
                extract_cmd = f"7z x -y -o{extract_dir} {archive_file}"
        else:
            extract_cmd = f"tar -xzf {archive_file} -C {extract_dir} 2>/dev/null || tar -xf {archive_file} -C {extract_dir}"

        success, _, stderr = await ssh_manager.execute_command(extract_cmd, timeout=120)

        if not success:
            await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
            await progress(f"Failed to extract archive: {stderr}", "error")
            await notify_install_result(False, f"Failed to extract archive: {stderr}")
            return GitHubPluginInstallResponse(
                success=False, message=f"Failed to extract archive: {stderr}"
            )

        await progress("Extraction complete, analyzing archive structure...")

        # Check if addons directory exists in extracted content
        addons_check = f"test -d {extract_dir}/addons && echo 'addons_found'"
        success, addons_output, _ = await ssh_manager.execute_command(addons_check)
        has_addons = "addons_found" in addons_output

        # Determine source directory for copy
        if has_addons:
            # Archive has proper structure (addons/, cfg/, etc.)
            source_dir = extract_dir
            await progress("Found addons/ directory at root level")
        else:
            # Check if there's a single subdirectory that contains addons
            find_cmd = f"find {extract_dir} -maxdepth 2 -type d -name 'addons' | head -1"
            success, find_output, _ = await ssh_manager.execute_command(find_cmd)

            if find_output.strip():
                # Found addons in subdirectory
                addons_path = find_output.strip()
                source_dir = addons_path.rsplit("/addons", 1)[0]
                await progress("Found addons/ directory in subdirectory")
            elif request.custom_install_path:
                # No addons directory found, but custom install path is specified
                # Extract to the custom path (e.g., 'addons')
                safe_custom_path = request.custom_install_path.strip().strip("/")

                # Validate custom path to prevent path traversal
                if ".." in safe_custom_path or safe_custom_path.startswith("/"):
                    await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
                    error_msg = "Invalid custom install path specified"
                    await progress(error_msg, "error")
                    await notify_install_result(False, error_msg)
                    return GitHubPluginInstallResponse(success=False, message=error_msg)

                # Build exclusion patterns for files and directories
                exclude_raw_patterns = []

                # Exclude specified files (new preferred method)
                for exclude_file in request.exclude_files:
                    # Sanitize file path
                    safe_file = exclude_file.strip().strip("/")
                    if safe_file and ".." not in safe_file:
                        exclude_raw_patterns.append(safe_file)

                # Also support excluding directories for backward compatibility
                for exclude_dir in request.exclude_dirs:
                    # Sanitize directory name
                    safe_dir = exclude_dir.strip().strip("/")
                    if safe_dir and ".." not in safe_dir:
                        exclude_raw_patterns.append(safe_dir)
                        exclude_raw_patterns.append(f"{safe_dir}/")
                        exclude_raw_patterns.append(f"{safe_dir}/*")

                if exclude_raw_patterns:
                    exclude_count = len(request.exclude_files) + len(request.exclude_dirs)
                    await progress(f"Excluding {exclude_count} item(s) from installation")

                # Create the target directory structure
                target_custom_dir = f"{csgo_dir}/{safe_custom_path}"
                mkdir_cmd = f"mkdir -p {target_custom_dir}"
                await ssh_manager.execute_command(mkdir_cmd)

                # Copy with exclusions
                rsync_check = "command -v rsync"
                success_check, rsync_path, _ = await ssh_manager.execute_command(rsync_check)

                if rsync_path.strip():
                    # Use rsync for better control
                    if exclude_raw_patterns:
                        await progress(f"Applying {len(exclude_raw_patterns)} exclusion pattern(s)")
                    copy_cmd = _build_plugin_copy_command(
                        extract_dir,
                        target_custom_dir,
                        exclude_raw_patterns,
                        use_rsync=True,
                    )
                else:
                    # Fallback to cp with tar for exclusions
                    if exclude_raw_patterns:
                        await progress(
                            f"Using tar with {len(exclude_raw_patterns)} exclusion pattern(s)"
                        )
                    copy_cmd = _build_plugin_copy_command(
                        extract_dir,
                        target_custom_dir,
                        exclude_raw_patterns,
                        use_rsync=False,
                    )

                logger.info(f"Custom path copy command: {copy_cmd}")
                success, _, stderr = await ssh_manager.execute_command(copy_cmd)

                if not success:
                    await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
                    error_msg = f"Failed to copy files to custom path: {stderr}"
                    await progress(error_msg, "error")
                    await notify_install_result(False, error_msg)
                    return GitHubPluginInstallResponse(success=False, message=error_msg)

                await progress(f"Extracted to custom path: {safe_custom_path}")

                # Cleanup and return success
                await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")

                # Count files after installation
                count_after_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
                _, count_after, _ = await ssh_manager.execute_command(count_after_cmd)
                count_after = int(count_after.strip()) if count_after.strip().isdigit() else 0

                await progress(
                    f"Installation complete! Custom path used: {safe_custom_path}", "success"
                )
                await notify_install_result(
                    True,
                    f"Plugin installed successfully to custom path: {safe_custom_path}",
                    count_after,
                )
                await record_installation()

                return GitHubPluginInstallResponse(
                    success=True,
                    message=f"Plugin installed successfully to custom path: {safe_custom_path}",
                    installed_files=count_after,
                )
            else:
                # No addons directory found - reject installation
                await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
                error_msg = "No addons/ directory found in archive. This does not appear to be a valid CS2 plugin package."
                await progress(error_msg, "error")
                await notify_install_result(False, error_msg)
                return GitHubPluginInstallResponse(success=False, message=error_msg)

        # Build exclusion patterns for files and directories
        exclude_raw_patterns = []

        # Exclude specified files (new preferred method)
        for exclude_file in request.exclude_files:
            # Sanitize file path
            safe_file = exclude_file.strip().strip("/")
            if safe_file and ".." not in safe_file:
                exclude_raw_patterns.append(safe_file)

        # Also support excluding directories for backward compatibility
        for exclude_dir in request.exclude_dirs:
            # Sanitize directory name
            safe_dir = exclude_dir.strip().strip("/")
            if safe_dir and ".." not in safe_dir:
                exclude_raw_patterns.append(safe_dir)
                exclude_raw_patterns.append(f"{safe_dir}/")
                exclude_raw_patterns.append(f"{safe_dir}/*")

        if exclude_raw_patterns:
            exclude_count = len(request.exclude_files) + len(request.exclude_dirs)
            await progress(f"Excluding {exclude_count} item(s) from installation")

        # Count files before copy
        count_before_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
        _, count_before, _ = await ssh_manager.execute_command(count_before_cmd)
        count_before = int(count_before.strip()) if count_before.strip().isdigit() else 0

        await progress("Installing plugin files...")

        # Copy files using rsync for better control
        rsync_check = "command -v rsync"
        success, rsync_path, _ = await ssh_manager.execute_command(rsync_check)

        if rsync_path.strip():
            # Use rsync for better control
            if exclude_raw_patterns:
                await progress(f"Applying {len(exclude_raw_patterns)} exclusion pattern(s)")
            copy_cmd = _build_plugin_copy_command(
                source_dir,
                csgo_dir,
                exclude_raw_patterns,
                use_rsync=True,
            )
        else:
            # Fallback to cp with tar for exclusions
            if exclude_raw_patterns:
                await progress(f"Using tar with {len(exclude_raw_patterns)} exclusion pattern(s)")
            copy_cmd = _build_plugin_copy_command(
                source_dir,
                csgo_dir,
                exclude_raw_patterns,
                use_rsync=False,
            )

        logger.info(f"Copy command: {copy_cmd}")
        success, copy_output, stderr = await ssh_manager.execute_command(copy_cmd, timeout=120)

        # Count files after copy
        count_after_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
        _, count_after, _ = await ssh_manager.execute_command(count_after_cmd)
        count_after = int(count_after.strip()) if count_after.strip().isdigit() else 0

        installed_files = count_after - count_before if count_after > count_before else 0

        # Cleanup - use the correct temp directory
        await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
        await progress("Cleanup complete")

        if not success:
            await progress(f"Failed to copy files: {stderr}", "error")
            await notify_install_result(False, f"Failed to copy files: {stderr}", installed_files)
            return GitHubPluginInstallResponse(
                success=False,
                message=f"Failed to copy files: {stderr}",
                installed_files=installed_files,
            )

        success_msg = f"Plugin installed successfully! {installed_files} files installed. Restart server to apply changes."
        await progress(success_msg, "complete")
        await notify_install_result(True, success_msg, installed_files)
        await record_installation()

        return GitHubPluginInstallResponse(
            success=True, message=success_msg, installed_files=installed_files
        )

    except Exception as e:
        logger.error(f"Error installing plugin: {e}")
        await progress(f"Installation error: {str(e)}", "error")
        await notify_install_result(False, f"Installation error: {str(e)}")
        return GitHubPluginInstallResponse(success=False, message=f"Installation error: {str(e)}")
    finally:
        await ssh_manager.disconnect()
