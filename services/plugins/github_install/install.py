"""Orchestrate GitHub plugin download, extract, and copy over SSH."""

from __future__ import annotations

import logging
import shlex
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from modules import GitHubPluginInstallRequest, GitHubPluginInstallResponse, User
from services.plugins.github_install.context import GithubInstallContext, host
from services.plugins.github_install.deploy import deploy_plugin_archive
from services.plugins.github_install.download import download_plugin_archive

logger = logging.getLogger(__name__)


async def install_github_plugin(
    server_id: int,
    request: GitHubPluginInstallRequest,
    db: AsyncSession,
    current_user: User,
    ai_progress: Callable[[str, str], Awaitable[None]] | None = None,
    operation_id: str | None = None,
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

    server = await host.get_server_for_user(db, server_id, current_user)

    async def progress(msg: str, msg_type: str = "status"):
        """Send progress update via WebSocket"""
        await host.send_deployment_update(server_id, msg_type, msg)
        if ai_progress is not None:
            try:
                await ai_progress(msg, msg_type)
            except Exception:
                pass

    async def notify_install_result(
        success: bool,
        message: str,
        installed_files: Optional[int] = None,
    ) -> None:
        if request.suppress_notification:
            return
        details: dict[str, Any] = {
            "Download URL": request.download_url,
        }
        if installed_files is not None:
            details["Installed Files"] = installed_files

        host.discord_notification_service.queue_notify(
            server,
            host.EVENT_PLUGIN_UPDATE,
            "install_github_plugin",
            success,
            message,
            title=f"GitHub plugin install {'completed' if success else 'failed'}",
            details=details,
        )

    async def record_installation() -> None:
        if not request.record_installation or not request.repo_url:
            return
        from services.plugins.tracking import (
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
            installed_asset_name=request.asset_name,
            asset_glob=request.asset_glob
            or derive_asset_glob(request.asset_name, request.release_tag),
            custom_install_path=request.custom_install_path,
            exclude_dirs=request.exclude_dirs,
            exclude_files=request.exclude_files,
        )

    ssh_manager = host.SSHManager()
    success, msg = await ssh_manager.connect(server)
    if not success:
        await progress(f"SSH connection failed: {msg}", "error")
        await notify_install_result(False, f"SSH connection failed: {msg}")
        return GitHubPluginInstallResponse(success=False, message=f"SSH connection failed: {msg}")

    # Never share staging files by server ID alone.  A run ID is stable across
    # retries, while direct API callers receive a fresh UUID automatically.
    remote_temp_dir = host._remote_plugin_temp_dir(server_id, operation_id)

    try:
        await progress("Connected to server, starting plugin installation...")
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
        ctx = GithubInstallContext(
            server=server,
            request=request,
            current_user=current_user,
            ssh_manager=ssh_manager,
            remote_temp_dir=remote_temp_dir,
            progress=progress,
            notify_install_result=notify_install_result,
            record_installation=record_installation,
            csgo_dir=csgo_dir,
        )
        downloaded = await download_plugin_archive(ctx)
        if isinstance(downloaded, GitHubPluginInstallResponse):
            return downloaded
        archive_file, archive_type, _secure_plan_download = downloaded
        return await deploy_plugin_archive(ctx, archive_file, archive_type)
    except Exception as e:
        logger.error(f"Error installing plugin: {e}")
        await progress(f"Installation error: {str(e)}", "error")
        await notify_install_result(False, f"Installation error: {str(e)}")
        return GitHubPluginInstallResponse(success=False, message=f"Installation error: {str(e)}")
    finally:
        try:
            # The path is operation-scoped, so cleanup cannot remove another
            # session's archive even when sessions target the same server.
            await ssh_manager.execute_command(
                f"rm -rf -- {shlex.quote(remote_temp_dir)}", timeout=20
            )
        except Exception as cleanup_error:
            logger.warning(
                "Failed to clean plugin staging directory %s: %s", remote_temp_dir, cleanup_error
            )
        await ssh_manager.disconnect()
