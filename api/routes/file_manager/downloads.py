"""File Manager downloads endpoints."""

# ruff: noqa: F403,F405

from api.http_resource import (
    ApplicationHTTP as _ApplicationHTTP,
)
from api.http_resource import (
    as_application_http as _as_application_http,
)
from api.http_resource import (
    resolve_application_http as _resolve_application_http,
)

from .common import *

router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])


@router.post(
    "/download-url",
    response_model=DownloadUrlStartedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def download_archive_from_url(
    server_id: int,
    request: DownloadUrlRequest,
    http_request: Request,
    http_resource: _ApplicationHTTP | object = Depends(_resolve_application_http),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """Start downloading an HTTP(S) archive directly on the SSH host."""
    lock_service = _coerce_maintenance_lock_service(lock_service)
    server = await get_server_for_user(server_id, db, current_user)
    ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
    ssh_manager_factory = _bound_ssh_manager_factory(ssh_manager, SSHManager)
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

    user_id = current_user.id
    user_is_admin = current_user.is_admin
    github_token = await get_effective_github_token(db, current_user)
    # End every request DB transaction before SSH or outbound HTTP starts.
    await db.commit()

    # Resolve existing ancestors on the SSH host so an in-tree symlink cannot
    # redirect the write outside the canonical game directory.
    try:
        valid, validation_error = await ssh_manager.validate_path_within_base(
            server.game_directory,
            target_path or destination_path,
            server,
            allow_missing=True,
        )
    finally:
        await _disconnect_ssh_manager(ssh_manager, operation="URL download validation")
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
            "user_id": user_id,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "message": None,
            "error": None,
        }

    session_factory, _supervisor = _background_resources(http_request)
    coroutine = _run_bounded_file_task(
        user_id,
        _run_download_url_task,
        task_id,
        url,
        destination_path,
        target_path,
        server_id,
        user_id,
        user_is_admin,
        request.overwrite,
        github_token,
        session_factory,
        lock_service,
        _as_application_http(http_resource),
        ssh_manager_factory,
    )
    try:
        task = _spawn_file_task(
            http_request,
            coroutine,
            name=f"file-url-download-{task_id}",
        )
    except Exception:
        coroutine.close()
        async with download_url_tasks_lock:
            download_url_tasks.pop(task_id, None)
        raise
    async with download_url_tasks_lock:
        _download_url_task_refs[task_id] = task

    return {
        "success": True,
        "task_id": task_id,
        "status": "pending",
        "target_path": target_path,
    }


@router.get(
    "/download-url/status/{task_id}",
    response_model=DownloadUrlStatusResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    ),
)
async def get_download_url_status(
    server_id: int,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
