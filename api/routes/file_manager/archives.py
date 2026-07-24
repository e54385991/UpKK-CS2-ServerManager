"""File Manager archives endpoints."""

# ruff: noqa: F403,F405

from .common import *

router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])


@router.post(
    "/archive/inspect",
    response_model=ArchiveInspectionResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def inspect_archive(
    server_id: int,
    request: InspectArchiveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """Inspect a remote archive and return its safe directory entries."""
    server = await get_server_for_user(server_id, db, current_user)
    archive_path = posixpath.normpath(request.archive_path)
    if not is_path_safe(server.game_directory, archive_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: archive path is outside server directory",
        )

    ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
    try:
        success, archive_info, error = await ssh_manager.inspect_archive(archive_path, server)
    finally:
        await ssh_manager.disconnect()
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )

    return {
        "archive_type": archive_info["archive_type"],
        "folders": archive_info["folders"],
        "entry_count": archive_info["entry_count"],
    }


@router.post(
    "/extract",
    response_model=ExtractionStartedResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def extract_archive(
    server_id: int,
    request: ExtractArchiveRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """
    Extract archive file (zip, tar, tar.gz, etc.) asynchronously.

    Returns immediately with a task_id that can be used to poll for status.
    The extraction runs in the background so the web UI doesn't block.
    """
    server = await get_server_for_user(server_id, db, current_user)
    lock_service = _coerce_maintenance_lock_service(lock_service)
    ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
    ssh_manager_factory = _bound_ssh_manager_factory(ssh_manager, SSHManager)

    archive_path = posixpath.normpath(request.archive_path)
    source_folder = _normalize_source_folder(request.source_folder)
    # If no destination specified or empty string, extract to the same directory as the archive
    if not request.destination_path or request.destination_path.strip() == "":
        destination_path = posixpath.dirname(archive_path)
    else:
        destination_path = posixpath.normpath(request.destination_path)

    # Security check
    if not is_path_safe(server.game_directory, archive_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: archive path is outside server directory",
        )

    if not is_path_safe(server.game_directory, destination_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination path is outside server directory",
        )

    # Clean up old extraction tasks periodically
    await _cleanup_old_extraction_tasks()

    # Generate unique task ID
    task_id = str(uuid.uuid4())

    # Initialize task status with lock
    async with extraction_tasks_lock:
        extraction_tasks[task_id] = {
            "status": "pending",
            "archive_path": archive_path,
            "destination_path": destination_path,
            "source_folder": source_folder,
            "strip_source_folder": bool(request.strip_source_folder and source_folder),
            "server_id": server_id,
            "user_id": current_user.id,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "message": None,
            "error": None,
        }

    session_factory, _supervisor = _background_resources(http_request)
    coroutine = _run_bounded_file_task(
        current_user.id,
        _run_extraction_task,
        task_id,
        archive_path,
        destination_path,
        server_id,
        current_user.id,
        current_user.is_admin,
        request.overwrite,
        source_folder,
        bool(request.strip_source_folder and source_folder),
        session_factory,
        lock_service,
        ssh_manager_factory,
    )
    try:
        task = _spawn_file_task(
            http_request,
            coroutine,
            name=f"file-extract-{task_id}",
        )
    except Exception:
        coroutine.close()
        async with extraction_tasks_lock:
            extraction_tasks.pop(task_id, None)
        raise

    # Store task reference for proper cleanup/tracking
    async with extraction_tasks_lock:
        _extraction_task_refs[task_id] = task

    logger.info(f"[Extraction] Created task {task_id} for archive {archive_path}")

    return {
        "success": True,
        "task_id": task_id,
        "message": "Extraction started",
        "status": "pending",
        "destination": destination_path,
    }


@router.get(
    "/extract/status/{task_id}",
    response_model=ExtractionStatusResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    ),
)
async def get_extraction_status(
    server_id: int,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get the status of an extraction task.

    Returns the current status (pending, running, completed, failed) and any error message.
    """
    # Verify user has access to this server
    await get_server_for_user(server_id, db, current_user)

    async with extraction_tasks_lock:
        if task_id not in extraction_tasks:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction task not found or has expired",
            )

        task_info = extraction_tasks[task_id].copy()  # Copy to avoid holding lock during response

    # Verify the task belongs to this server and user
    if task_info.get("server_id") != server_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Task does not belong to this server"
        )

    if task_info.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Task does not belong to this user"
        )

    # Calculate elapsed time
    elapsed = None
    if task_info.get("started_at"):
        end_time = task_info.get("completed_at") or time.time()
        elapsed = round(end_time - task_info["started_at"], 1)

    return {
        "task_id": task_id,
        "status": task_info["status"],
        "archive_path": task_info["archive_path"],
        "destination_path": task_info["destination_path"],
        "source_folder": task_info.get("source_folder"),
        "strip_source_folder": task_info.get("strip_source_folder", False),
        "message": task_info.get("message"),
        "error": task_info.get("error"),
        "elapsed_seconds": elapsed,
    }
