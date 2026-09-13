"""Background download and archive-extraction workers."""

from __future__ import annotations

from typing import Dict, Optional

from modules import Server
from services.compat import LateBoundModule
from services.ssh_manager import SSHManager

host = LateBoundModule("api.routes.file_manager.common")


async def _run_bounded_file_task(user_id: int, callback) -> None:
    async with host._file_task_limiter.slot(user_id):
        await callback()


async def shutdown_background_tasks() -> None:
    """Compatibility wrapper for lifecycle-owned task cleanup."""
    await host.file_task_registry.shutdown()
    host._download_url_task_refs.clear()
    host._extraction_task_refs.clear()


def _cleanup_temp_file(path: str) -> None:
    """Remove a temporary file after FileResponse finishes sending it."""
    try:
        if host.os.path.exists(path):
            host.os.unlink(path)
    except OSError:
        host.logger.warning("Failed to clean up temporary download file: %s", path, exc_info=True)


def _download_headers(filename: str, file_size: Optional[int] = None) -> Dict[str, str]:
    """Build attachment headers with UTF-8 filename support."""
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii") or "download"
    ascii_filename = ascii_filename.replace("\\", "_").replace('"', "_")
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{host.quote(filename)}"
        )
    }
    if file_size is not None:
        headers["Content-Length"] = str(file_size)
    return headers


async def _run_download_url_task(
    task_id: str,
    url: str,
    destination_path: str,
    target_path: Optional[str],
    server: Server,
    overwrite: bool,
    github_token: Optional[str],
):
    """Download an archive on the SSH host without retaining its URL in status."""
    ssh_manager: Optional[SSHManager] = None

    async def update_target_path(resolved_target_path: str) -> None:
        async with host.download_url_tasks_lock:
            task_info = host.download_url_tasks.get(task_id)
            if task_info is not None:
                task_info["target_path"] = resolved_target_path

    try:
        async with host.download_url_tasks_lock:
            host.download_url_tasks[task_id]["status"] = "running"
            host.download_url_tasks[task_id]["started_at"] = host.time.time()

        manager = host.SSHManager()
        ssh_manager = manager
        connected, connection_error = await manager.connect(server)
        if not connected:
            raise RuntimeError(f"Connection failed: {connection_error}")

        is_github_artifact = host._parse_github_actions_artifact_url(url) is not None
        download_url = url
        if is_github_artifact:
            download_url, artifact_filename = await host._resolve_github_actions_artifact(
                url,
                github_token,
            )
            if target_path is None:
                target_path = str(host.remote_join(destination_path, artifact_filename))
                await update_target_path(target_path)

        host.logger.info(
            "[URL Download] Starting task %s -> %s",
            task_id,
            target_path or destination_path,
        )
        success, error = await manager.download_url_to_file(
            download_url,
            target_path,
            server,
            overwrite=overwrite,
            destination_path=destination_path,
            resolved_target_callback=update_target_path,
        )

        # GitHub's signed object-storage redirect expires after one minute. If
        # curl reached it too late, fetch a fresh redirect and retry exactly
        # once. Non-transfer failures (unsafe path, conflict, missing curl) do
        # not benefit from another authenticated API request.
        if is_github_artifact and not success and error.startswith("Download failed:"):
            download_url, _ = await host._resolve_github_actions_artifact(url, github_token)
            success, error = await manager.download_url_to_file(
                download_url,
                target_path,
                server,
                overwrite=overwrite,
                destination_path=destination_path,
                resolved_target_callback=update_target_path,
            )

        # The SSH host only ever receives an expiring signed URL, never this
        # credential. Drop the coroutine's local token reference after use.
        github_token = None

        async with host.download_url_tasks_lock:
            if success:
                host.download_url_tasks[task_id]["status"] = "completed"
                host.download_url_tasks[task_id]["message"] = "Archive downloaded successfully"
            else:
                host.download_url_tasks[task_id]["status"] = "failed"
                host.download_url_tasks[task_id]["error"] = error
            host.download_url_tasks[task_id]["completed_at"] = host.time.time()
    except Exception as exc:
        host.logger.exception("[URL Download] Task %s failed", task_id)
        async with host.download_url_tasks_lock:
            if task_id in host.download_url_tasks:
                host.download_url_tasks[task_id]["status"] = "failed"
                host.download_url_tasks[task_id]["error"] = str(exc)
                host.download_url_tasks[task_id]["completed_at"] = host.time.time()
    finally:
        if ssh_manager is not None:
            try:
                await ssh_manager.disconnect()
            except Exception:
                host.logger.warning(
                    "[URL Download] Failed to release SSH connection for task %s",
                    task_id,
                    exc_info=True,
                )
        async with host.download_url_tasks_lock:
            host._download_url_task_refs.pop(task_id, None)


async def _cleanup_old_download_url_tasks():
    """Remove expired URL download task records and completed task references."""
    current_time = host.time.time()
    tasks_to_remove = []
    async with host.download_url_tasks_lock:
        for task_id, task_info in host.download_url_tasks.items():
            completed_at = task_info.get("completed_at")
            created_at = task_info.get("created_at")
            if (
                completed_at
                and current_time - completed_at > host.EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS
            ):
                tasks_to_remove.append(task_id)
            elif (
                not completed_at
                and created_at
                and current_time - created_at > host.EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS
            ):
                tasks_to_remove.append(task_id)

        for task_id in tasks_to_remove:
            host.download_url_tasks.pop(task_id, None)
            host._download_url_task_refs.pop(task_id, None)


async def _run_extraction_task(
    task_id: str,
    archive_path: str,
    destination_path: str,
    server: Server,
    overwrite: bool,
    source_folder: Optional[str],
    strip_source_folder: bool,
):
    """Background task to perform archive extraction"""
    ssh_manager: Optional[SSHManager] = None
    try:
        async with host.extraction_tasks_lock:
            host.extraction_tasks[task_id]["status"] = "running"
            host.extraction_tasks[task_id]["started_at"] = host.time.time()

        host.logger.info(
            f"[Extraction] Starting extraction task {task_id}: {archive_path} -> {destination_path}"
        )

        # Extract using SSH
        manager = host.SSHManager()
        ssh_manager = manager
        success, error = await manager.extract_archive(
            archive_path,
            destination_path,
            server,
            overwrite,
            source_folder=source_folder,
            strip_source_folder=strip_source_folder,
        )

        async with host.extraction_tasks_lock:
            if success:
                host.extraction_tasks[task_id]["status"] = "completed"
                host.extraction_tasks[task_id]["message"] = "Archive extracted successfully"
                host.logger.info(f"[Extraction] Task {task_id} completed successfully")
            else:
                host.extraction_tasks[task_id]["status"] = "failed"
                host.extraction_tasks[task_id]["error"] = error
                host.logger.error(f"[Extraction] Task {task_id} failed: {error}")

            host.extraction_tasks[task_id]["completed_at"] = host.time.time()

    except Exception as e:
        host.logger.exception(f"[Extraction] Task {task_id} encountered an exception")
        async with host.extraction_tasks_lock:
            host.extraction_tasks[task_id]["status"] = "failed"
            host.extraction_tasks[task_id]["error"] = str(e)
            host.extraction_tasks[task_id]["completed_at"] = host.time.time()
    finally:
        if ssh_manager is not None:
            try:
                await ssh_manager.disconnect()
            except Exception:
                host.logger.warning(
                    "[Extraction] Failed to release SSH connection for task %s",
                    task_id,
                    exc_info=True,
                )
        # Clean up task reference
        async with host.extraction_tasks_lock:
            if task_id in host._extraction_task_refs:
                del host._extraction_task_refs[task_id]


async def _cleanup_old_extraction_tasks():
    """Clean up extraction tasks older than configured thresholds"""
    current_time = host.time.time()
    tasks_to_remove = []

    async with host.extraction_tasks_lock:
        for task_id, task_info in host.extraction_tasks.items():
            # Remove completed/failed tasks older than threshold
            if task_info.get("completed_at"):
                if (
                    current_time - task_info["completed_at"]
                    > host.EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS
                ):
                    tasks_to_remove.append(task_id)
            # Remove pending tasks older than threshold (likely abandoned)
            elif task_info.get("created_at"):
                if (
                    current_time - task_info["created_at"]
                    > host.EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS
                ):
                    tasks_to_remove.append(task_id)

        for task_id in tasks_to_remove:
            del host.extraction_tasks[task_id]
            # Also clean up task reference if it exists
            if task_id in host._extraction_task_refs:
                del host._extraction_task_refs[task_id]
