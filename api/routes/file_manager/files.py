"""File Manager files endpoints."""

# ruff: noqa: F403,F405

from .common import *

router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])


@router.get(
    "",
    response_model=DirectoryListResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def list_directory(
    server_id: int,
    path: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """List directory contents"""
    server = await get_server_for_user(server_id, db, current_user)

    # Use server's game directory as base if no path specified
    if not path:
        path = server.game_directory

    # Security: ensure path is within server's directory
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    # List directory using SSH
    ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
    try:
        success, files, error = await ssh_manager.list_directory(path, server)
    finally:
        await _disconnect_ssh_manager(ssh_manager, operation="directory listing")

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    return DirectoryListResponse(path=path, files=files)


@router.get(
    "/content",
    response_model=FileContentResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def get_file_content(
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """Get file content for viewing/editing"""
    server = await get_server_for_user(server_id, db, current_user)

    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
    try:
        valid, validation_error = await ssh_manager.validate_path_within_base(
            server.game_directory,
            path,
            server,
            require_regular=True,
        )
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: {validation_error}",
            )
        success, content, error = await ssh_manager.read_file(path, server)
    finally:
        await _disconnect_ssh_manager(ssh_manager, operation="file content read")

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    return {"path": path, "content": content}


@router.put(
    "/content",
    response_model=FileActionResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def update_file_content(
    server_id: int,
    path: str,
    request: FileContentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """Update file content"""
    lock_service = _coerce_maintenance_lock_service(lock_service)
    server = await get_server_for_user(server_id, db, current_user)

    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    async with lock_service.get(
        server_id,
        operation="file_content_update",
        wait=False,
        ttl=7200,
    ):
        ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
        try:
            valid, validation_error = await ssh_manager.validate_path_within_base(
                server.game_directory,
                path,
                server,
                require_regular=True,
            )
            if not valid:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied: {validation_error}",
                )
            success, error = await ssh_manager.write_file(path, request.content, server)
        finally:
            await _disconnect_ssh_manager(ssh_manager, operation="file content update")

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    return {"success": True, "message": "File updated successfully"}


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_413_CONTENT_TOO_LARGE,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def upload_file(
    server_id: int,
    path: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """Upload file to server"""
    lock_service = _coerce_maintenance_lock_service(lock_service)
    server = await get_server_for_user(server_id, db, current_user)

    # Construct remote path
    remote_path = remote_join(path, file.filename)

    # Security check
    if not is_path_safe(server.game_directory, remote_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    # Save uploaded file to temp location
    temp_file = None
    try:
        # Create temporary file
        temp_fd, temp_path = tempfile.mkstemp()
        os.close(temp_fd)
        temp_file = temp_path

        # Stream to disk without buffering the full upload or blocking the event loop.
        uploaded_bytes = 0
        async with await anyio.open_file(temp_path, "wb") as target:
            while chunk := await file.read(1024 * 1024):
                uploaded_bytes += len(chunk)
                if uploaded_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="Uploaded file exceeds the 4 GiB limit",
                    )
                await target.write(chunk)

        async with lock_service.get(
            server_id,
            operation="file_upload",
            wait=False,
            ttl=7200,
        ):
            ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
            try:
                success, error = await ssh_manager.upload_file(temp_path, remote_path, server)
            finally:
                await _disconnect_ssh_manager(ssh_manager, operation="file upload")

        if not success:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

        return {
            "success": True,
            "message": "File uploaded successfully",
            "path": remote_path,
            "filename": file.filename,
        }

    finally:
        # Clean up temp file
        if temp_file and os.path.exists(temp_file):
            os.unlink(temp_file)


@router.get(
    "/download",
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def download_file(
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user_for_download),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """Download file from server"""
    server = await get_server_for_user(server_id, db, current_user)

    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    filename = posixpath.basename(path) or "download"
    ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
    disconnect_deferred = False
    disconnect_lock = asyncio.Lock()
    disconnected = False
    temp_path = None
    response_owns_temp = False

    async def disconnect_once() -> None:
        nonlocal disconnected
        async with disconnect_lock:
            if disconnected:
                return
            disconnected = True
            await _disconnect_ssh_manager(ssh_manager, operation="file download")

    try:
        size_success, file_size, size_error = await ssh_manager.get_file_size(path, server)
        if not size_success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=size_error
            )

        if file_size is None or file_size > STREAMING_DOWNLOAD_THRESHOLD_BYTES:

            async def remote_file_iterator():
                try:
                    async for chunk in ssh_manager.stream_file(path, server):
                        yield chunk
                finally:
                    await disconnect_once()

            response = StreamingResponse(
                remote_file_iterator(),
                media_type="application/octet-stream",
                headers=_download_headers(filename, file_size),
                background=BackgroundTask(disconnect_once),
            )
            disconnect_deferred = True
            return response

        temp_fd, temp_path = tempfile.mkstemp()
        os.close(temp_fd)

        success, error = await ssh_manager.download_file(path, temp_path, server)
        if not success:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

        response = FileResponse(
            path=temp_path,
            filename=filename,
            media_type="application/octet-stream",
            headers=_download_headers(filename, file_size),
            background=BackgroundTask(_cleanup_temp_file, temp_path),
        )
        response_owns_temp = True
        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error downloading file: {str(e)}",
        ) from e
    finally:
        if not disconnect_deferred:
            await disconnect_once()
        if temp_path is not None and not response_owns_temp:
            _cleanup_temp_file(temp_path)


@router.post(
    "/download-ticket",
    response_model=DownloadTicketResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def create_download_ticket(
    server_id: int,
    request: DownloadTicketRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a short-lived one-time ticket for browser-native downloads."""
    server = await get_server_for_user(server_id, db, current_user)

    if not is_path_safe(server.game_directory, request.path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    try:
        ticket = await _create_download_ticket(
            http_request,
            current_user.id,
            server_id,
            request.path,
        )
    except DownloadTicketStoreUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {"ticket": ticket, "expires_in": DOWNLOAD_TICKET_TTL_SECONDS}


@router.post(
    "/mkdir",
    response_model=DirectoryCreatedResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def create_directory(
    server_id: int,
    path: str,
    request: CreateDirectoryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """Create a new directory"""
    lock_service = _coerce_maintenance_lock_service(lock_service)
    server = await get_server_for_user(server_id, db, current_user)

    directory_name = _validate_direct_child_name(request.name, "Directory name")

    # Construct full path
    new_dir_path = remote_join(path, directory_name)

    # Security check
    if not is_path_safe(server.game_directory, new_dir_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    async with lock_service.get(
        server_id,
        operation="directory_create",
        wait=False,
        ttl=7200,
    ):
        ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
        try:
            success, error = await ssh_manager.create_directory(new_dir_path, server)
        finally:
            await _disconnect_ssh_manager(ssh_manager, operation="directory creation")

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    return {"success": True, "message": "Directory created successfully", "path": new_dir_path}


@router.delete(
    "",
    response_model=FileActionResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def delete_path(
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """Delete file or directory"""
    lock_service = _coerce_maintenance_lock_service(lock_service)
    server = await get_server_for_user(server_id, db, current_user)

    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    # Don't allow deleting the root game directory
    if posixpath.normpath(path) == posixpath.normpath(server.game_directory):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete server root directory"
        )

    async with lock_service.get(
        server_id,
        operation="file_delete",
        wait=False,
        ttl=7200,
    ):
        ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
        try:
            success, error = await ssh_manager.delete_path(path, server)
        finally:
            await _disconnect_ssh_manager(ssh_manager, operation="file deletion")

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    return {"success": True, "message": "Deleted successfully"}


@router.post(
    "/rename",
    response_model=FileRenamedResponse,
    status_code=status.HTTP_200_OK,
    responses=file_error_responses(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
)
async def rename_file_or_directory(
    server_id: int,
    path: str,
    request: RenameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
):
    """Rename file or directory"""
    lock_service = _coerce_maintenance_lock_service(lock_service)
    server = await get_server_for_user(server_id, db, current_user)

    # Construct full paths
    old_path = remote_join(path, request.old_name)
    new_path = remote_join(path, request.new_name)

    # Security check - both paths must be within server directory
    if not is_path_safe(server.game_directory, old_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: source path is outside server directory",
        )

    if not is_path_safe(server.game_directory, new_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination path is outside server directory",
        )

    # Don't allow renaming the root game directory
    if posixpath.normpath(old_path) == posixpath.normpath(server.game_directory):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot rename server root directory"
        )

    async with lock_service.get(
        server_id,
        operation="file_rename",
        wait=False,
        ttl=7200,
    ):
        ssh_manager = _coerce_ssh_manager(ssh_manager, SSHManager)
        try:
            success, error = await ssh_manager.rename_path(old_path, new_path, server)
        finally:
            await _disconnect_ssh_manager(ssh_manager, operation="file rename")

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    return {"success": True, "message": "Renamed successfully", "new_path": new_path}
