"""File Manager files endpoints."""

# ruff: noqa: F403,F405

from fastapi import Request

from api.dependencies import ActiveUser, DatabaseSession
from services.audit_log_service import record_audit_event

from .common import *

transfer_router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])
mutation_router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])


@transfer_router.get("", response_model=DirectoryListResponse)
async def list_directory(
    server_id: int,
    path: Optional[str] = None,
    db: DatabaseSession = None,
    current_user: ActiveUser = None,
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
    ssh_manager = SSHManager()
    success, files, error = await ssh_manager.list_directory(path, server)

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    return DirectoryListResponse(path=path, files=files)


@transfer_router.get("/content")
async def get_file_content(
    server_id: int,
    path: str,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Get file content for viewing/editing"""
    server = await get_server_for_user(server_id, db, current_user)

    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    # Read file using SSH
    ssh_manager = SSHManager()
    success, content, error = await ssh_manager.read_file(path, server)

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    return {"path": path, "content": content}


@transfer_router.put("/content")
async def update_file_content(
    server_id: int,
    path: str,
    request: FileContentRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
):
    """Update file content"""
    server = await get_server_for_user(server_id, db, current_user)

    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    # Write file using SSH
    ssh_manager = SSHManager()
    success, error = await ssh_manager.write_file(path, request.content, server)

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    await record_audit_event(
        category="files",
        action="files.edit",
        status="success",
        user=current_user,
        request=http_request,
        server_id=server_id,
        details={"path": path, "bytes": len((request.content or "").encode("utf-8"))},
    )
    return {"success": True, "message": "File updated successfully"}


@transfer_router.post("/upload")
async def upload_file(
    server_id: int,
    path: str,
    file: UploadFile = File(...),
    db: DatabaseSession = None,
    current_user: ActiveUser = None,
    relative_path: Optional[str] = Query(default=None, max_length=1500),
    http_request: Request = None,
):
    """Upload file to server"""
    server = await get_server_for_user(server_id, db, current_user)

    # Construct remote path
    remote_name = safe_relative_upload_path(relative_path, file.filename)
    remote_path = remote_join(path, remote_name)

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

        # Upload to server using SSH
        ssh_manager = SSHManager()
        try:
            success, error = await ssh_manager.upload_file(temp_path, remote_path, server)
        finally:
            await ssh_manager.disconnect()

        if not success:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

        await record_audit_event(
            category="files",
            action="files.upload",
            status="success",
            user=current_user,
            request=http_request,
            server_id=server_id,
            details={"path": remote_path, "size": uploaded_bytes},
        )
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


@transfer_router.get("/download")
async def download_file(
    server_id: int,
    path: str,
    db: DatabaseSession,
    current_user: DownloadUser,
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
    ssh_manager = SSHManager()

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
                    await ssh_manager.disconnect()

            return StreamingResponse(
                remote_file_iterator(),
                media_type="application/octet-stream",
                headers=_download_headers(filename, file_size),
            )

        temp_fd, temp_path = tempfile.mkstemp()
        os.close(temp_fd)

        success, error = await ssh_manager.download_file(path, temp_path, server)
        if not success:
            _cleanup_temp_file(temp_path)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

        await ssh_manager.disconnect()

        return FileResponse(
            path=temp_path,
            filename=filename,
            media_type="application/octet-stream",
            headers=_download_headers(filename, file_size),
            background=BackgroundTask(_cleanup_temp_file, temp_path),
        )

    except HTTPException:
        await ssh_manager.disconnect()
        raise
    except Exception as e:
        await ssh_manager.disconnect()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error downloading file: {str(e)}",
        ) from e


@transfer_router.post("/download-ticket")
async def create_download_ticket(
    server_id: int,
    request: DownloadTicketRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Create a short-lived one-time ticket for browser-native downloads."""
    server = await get_server_for_user(server_id, db, current_user)

    if not is_path_safe(server.game_directory, request.path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )

    ticket = await _create_download_ticket(current_user.id, server_id, request.path)
    return {"ticket": ticket, "expires_in": DOWNLOAD_TICKET_TTL_SECONDS}


@mutation_router.post("/mkdir")
async def create_directory(
    server_id: int,
    path: str,
    request: CreateDirectoryRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
):
    """Create a new directory"""
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

    # Create directory using SSH
    ssh_manager = SSHManager()
    try:
        success, error = await ssh_manager.create_directory(new_dir_path, server)
    finally:
        await ssh_manager.disconnect()

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    await record_audit_event(
        category="files",
        action="files.mkdir",
        status="success",
        user=current_user,
        request=http_request,
        server_id=server_id,
        details={"path": new_dir_path},
    )
    return {"success": True, "message": "Directory created successfully", "path": new_dir_path}


@mutation_router.delete("")
async def delete_path(
    server_id: int,
    path: str,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
):
    """Delete file or directory"""
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

    # Delete using SSH
    ssh_manager = SSHManager()
    success, error = await ssh_manager.delete_path(path, server)

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    await record_audit_event(
        category="files",
        action="files.delete",
        status="success",
        user=current_user,
        request=http_request,
        server_id=server_id,
        details={"path": path},
    )
    return {"success": True, "message": "Deleted successfully"}


@mutation_router.post("/rename")
async def rename_file_or_directory(
    server_id: int,
    path: str,
    request: RenameRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
):
    """Rename file or directory"""
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

    # Rename using SSH
    ssh_manager = SSHManager()
    success, error = await ssh_manager.rename_path(old_path, new_path, server)

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    await record_audit_event(
        category="files",
        action="files.rename",
        status="success",
        user=current_user,
        request=http_request,
        server_id=server_id,
        details={"path": old_path, "new_path": new_path},
    )
    return {"success": True, "message": "Renamed successfully", "new_path": new_path}


@mutation_router.post("/copy")
async def copy_paths(
    server_id: int,
    request: CopyPathsRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    http_request: Request,
):
    """Copy files or directories into a destination folder."""
    server = await get_server_for_user(server_id, db, current_user)
    destination = posixpath.normpath((request.destination or "").strip())
    sources = [
        posixpath.normpath(item.strip()) for item in request.sources if item and item.strip()
    ]
    if not destination or destination == ".":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="destination is required",
        )
    if not sources or len(sources) > 50:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provide between 1 and 50 source paths",
        )
    if not is_path_safe(server.game_directory, destination):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination is outside server directory",
        )
    copied: list[str] = []
    ssh_manager = SSHManager()
    try:
        for source_path in sources:
            if not is_path_safe(server.game_directory, source_path):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: source is outside server directory",
                )
            success, new_path, error = await ssh_manager.copy_into_directory(
                source_path, destination, server
            )
            if not success:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)
            copied.append(new_path)
    finally:
        await ssh_manager.disconnect()
    await record_audit_event(
        category="files",
        action="files.copy",
        status="success",
        user=current_user,
        request=http_request,
        server_id=server_id,
        details={"destination": destination, "paths": copied, "count": len(copied)},
    )
    return {
        "success": True,
        "message": "Copied successfully",
        "paths": copied,
        "path": copied[-1] if copied else destination,
    }


router = APIRouter()
for _router in (transfer_router, mutation_router):
    router.include_router(_router)
