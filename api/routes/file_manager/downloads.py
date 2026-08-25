"""File Manager downloads endpoints."""

# ruff: noqa: F403,F405

from api.dependencies import ActiveUser, DatabaseSession

from .common import *

router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])


@router.post("/download-url", status_code=status.HTTP_202_ACCEPTED)
async def download_archive_from_url(
    server_id: int,
    request: DownloadUrlRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Start downloading an HTTP(S) archive directly on the SSH host."""
    server = await get_server_for_user(server_id, db, current_user)
    url = _validate_download_url(request.url)

    if not request.destination_path or not request.destination_path.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="destination_path cannot be empty",
        )
    destination_path = posixpath.normpath(request.destination_path)
    filename = _download_archive_filename(
        url,
        request.filename,
        allow_unresolved=True,
    )
    target_path = remote_join(destination_path, filename) if filename else None

    if not is_path_safe(server.game_directory, destination_path) or (
        target_path is not None and not is_path_safe(server.game_directory, target_path)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination path is outside server directory",
        )

    # Resolve existing ancestors on the SSH host so an in-tree symlink cannot
    # redirect the write outside the canonical game directory.
    validator = SSHManager()
    try:
        valid, validation_error = await validator.validate_path_within_base(
            server.game_directory,
            target_path or destination_path,
            server,
            allow_missing=True,
        )
    finally:
        await validator.disconnect()
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: {validation_error}",
        )

    await _cleanup_old_download_url_tasks()
    task_id = str(uuid.uuid4())
    async with download_url_tasks_lock:
        download_url_tasks[task_id] = {
            "status": "pending",
            "target_path": target_path,
            "server_id": server_id,
            "user_id": current_user.id,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "message": None,
            "error": None,
        }

    github_token = await get_effective_github_token(db, current_user)
    task = asyncio.create_task(
        _run_bounded_file_task(
            current_user.id,
            lambda: _run_download_url_task(
                task_id,
                url,
                destination_path,
                target_path,
                server,
                request.overwrite,
                github_token,
            ),
        )
    )
    file_task_registry.add(task)
    async with download_url_tasks_lock:
        _download_url_task_refs[task_id] = task

    return {
        "success": True,
        "task_id": task_id,
        "status": "pending",
        "target_path": target_path,
    }


@router.get("/download-url/status/{task_id}")
async def get_download_url_status(
    server_id: int,
    task_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Return status for a URL download task owned by this user and server."""
    await get_server_for_user(server_id, db, current_user)
    async with download_url_tasks_lock:
        task_info = download_url_tasks.get(task_id)
        if task_info is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Download task not found or has expired",
            )
        task_info = task_info.copy()

    if task_info.get("server_id") != server_id or task_info.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Download task does not belong to this server and user",
        )

    elapsed = None
    if task_info.get("started_at"):
        end_time = task_info.get("completed_at") or time.time()
        elapsed = round(end_time - task_info["started_at"], 1)
    return {
        "task_id": task_id,
        "status": task_info["status"],
        "target_path": task_info["target_path"],
        "message": task_info.get("message"),
        "error": task_info.get("error"),
        "elapsed_seconds": elapsed,
    }
