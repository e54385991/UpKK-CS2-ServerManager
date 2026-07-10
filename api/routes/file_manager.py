"""
File manager routes for server file operations
"""
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Header, Query
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, SQLModel
from typing import List, Optional, Dict, Any
import os
import posixpath
import tempfile
import shutil
import asyncio
import uuid
import time
import logging
import ipaddress
from urllib.parse import quote, unquote, urlsplit
from jose import JWTError, jwt

from modules import Server, get_db, User, get_current_active_user, settings
from services import SSHManager

logger = logging.getLogger(__name__)

# Constants for extraction task cleanup
EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS = 3600  # 1 hour
EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS = 7200  # 2 hours
STREAMING_DOWNLOAD_THRESHOLD_BYTES = 3 * 1024 * 1024  # 3MB
DOWNLOAD_TICKET_TTL_SECONDS = 60
REMOTE_NAME_MAX_BYTES = 255
DOWNLOAD_URL_MAX_LENGTH = 4096

# In-memory storage for extraction task status
# Key: task_id, Value: dict with status, archive_path, destination_path, 
# server_id, user_id, timestamps, message, and error
# Protected by extraction_tasks_lock for thread-safe access
extraction_tasks: Dict[str, Dict[str, Any]] = {}

# Store task references for proper cleanup
# Also protected by extraction_tasks_lock
_extraction_task_refs: Dict[str, asyncio.Task] = {}

# Lock for thread-safe access to extraction_tasks and _extraction_task_refs
extraction_tasks_lock = asyncio.Lock()

# URL downloads use the same short-lived, process-local task model as archive
# extraction. The task record intentionally does not retain the URL because
# signed download URLs commonly contain credentials in their query string.
download_url_tasks: Dict[str, Dict[str, Any]] = {}
_download_url_task_refs: Dict[str, asyncio.Task] = {}
download_url_tasks_lock = asyncio.Lock()

# Short-lived one-time tickets let browser-native downloads authenticate
# without exposing the long-lived JWT in a URL.
download_tickets: Dict[str, Dict[str, Any]] = {}
download_tickets_lock = asyncio.Lock()

router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])


class FileInfo(SQLModel):
    """File information model"""
    name: str
    path: str
    type: str
    size: int
    modified: float
    permissions: str
    is_symlink: bool


class DirectoryListResponse(SQLModel):
    """Directory listing response"""
    path: str
    files: List[FileInfo]


class FileContentRequest(SQLModel):
    """File content update request"""
    content: str


class CreateDirectoryRequest(SQLModel):
    """Create directory request"""
    name: str


class DownloadTicketRequest(SQLModel):
    """Create a short-lived browser download ticket"""
    path: str


class DeleteRequest(SQLModel):
    """Delete file/directory request"""
    path: str


class RenameRequest(SQLModel):
    """Rename file/directory request"""
    old_name: str
    new_name: str


class ExtractArchiveRequest(SQLModel):
    """Extract archive request"""
    archive_path: str
    destination_path: Optional[str] = None
    overwrite: bool = False
    source_folder: Optional[str] = None
    strip_source_folder: bool = False


class DownloadUrlRequest(SQLModel):
    """Download an archive URL to a remote server directory."""
    url: str
    destination_path: str
    filename: Optional[str] = None
    overwrite: bool = False


class InspectArchiveRequest(SQLModel):
    """Inspect folders contained in a remote archive."""
    archive_path: str


async def get_server_for_user(server_id: int, db: AsyncSession, current_user: User) -> Server:
    """Helper to get server and verify ownership - admins can access any server"""
    if current_user.is_admin:
        server = await Server.get_by_id(db, server_id)
    else:
        server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server not found"
        )
    return server


async def _create_download_ticket(user_id: int, server_id: int, path: str) -> str:
    """Create a short-lived one-time ticket bound to a user, server and path."""
    now = time.monotonic()
    async with download_tickets_lock:
        expired_tickets = [
            ticket for ticket, info in download_tickets.items()
            if info["expires_at"] <= now
        ]
        for ticket in expired_tickets:
            download_tickets.pop(ticket, None)

        ticket = uuid.uuid4().hex
        download_tickets[ticket] = {
            "user_id": user_id,
            "server_id": server_id,
            "path": path,
            "expires_at": now + DOWNLOAD_TICKET_TTL_SECONDS,
        }
        return ticket


async def _consume_download_ticket(ticket: str, server_id: int, path: str) -> Optional[int]:
    """Consume and validate a one-time download ticket."""
    now = time.monotonic()
    async with download_tickets_lock:
        ticket_info = download_tickets.pop(ticket, None)

    if not ticket_info:
        return None
    if ticket_info["expires_at"] <= now:
        return None
    if ticket_info["server_id"] != server_id or ticket_info["path"] != path:
        return None
    return int(ticket_info["user_id"])


async def get_current_active_user_for_download(
    server_id: int,
    path: str,
    ticket: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Authenticate downloads with a one-time ticket or a normal bearer token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_id: Optional[int] = None

    if ticket:
        user_id = await _consume_download_ticket(ticket, server_id, path)
        if user_id is None:
            raise credentials_exception
    elif authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise credentials_exception

        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            user_id_str = payload.get("sub")
            if user_id_str is None:
                raise credentials_exception
            user_id = int(user_id_str)
        except (JWTError, ValueError):
            raise credentials_exception
    else:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    return user


def is_path_safe(base_path: str, requested_path: str) -> bool:
    """
    Verify that the requested path is within the server's directory
    Prevents path traversal attacks
    """
    # Remote game servers use POSIX paths even when the panel runs on Windows.
    base = posixpath.normpath(base_path)
    requested = posixpath.normpath(requested_path)
    
    # Check if requested path is the base path or a child of it
    return requested == base or requested.startswith(base.rstrip("/") + "/")


def remote_join(*parts: str) -> str:
    """Join remote server paths using POSIX separators."""
    return posixpath.normpath(posixpath.join(*parts))


def _validate_direct_child_name(name: str, label: str = "name") -> str:
    """Validate a single remote path component without changing its value."""
    if not isinstance(name, str) or not name or not name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} cannot be empty"
        )
    if name in (".", "..") or posixpath.basename(name) != name or "/" in name or "\\" in name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} must be a direct child name and cannot contain path separators"
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} cannot contain control characters"
        )
    if len(name.encode("utf-8")) > REMOTE_NAME_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} is too long (maximum {REMOTE_NAME_MAX_BYTES} UTF-8 bytes)"
        )
    return name


def _normalize_source_folder(source_folder: Optional[str]) -> Optional[str]:
    """Normalize and validate a directory name stored inside an archive."""
    if source_folder is None or not source_folder.strip():
        return None

    value = source_folder.strip().rstrip("/")
    while value.startswith("./"):
        value = value[2:]

    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_folder must be a safe relative POSIX directory path"
        )

    normalized = posixpath.normpath(value)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_folder cannot escape the archive root"
        )
    return normalized


def _validate_download_url(url: str) -> str:
    """Apply transport-level validation before passing a URL to remote curl."""
    if not isinstance(url, str) or not url or len(url) > DOWNLOAD_URL_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"URL is required and must not exceed {DOWNLOAD_URL_MAX_LENGTH} characters"
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="URL cannot contain control characters"
        )

    try:
        parsed = urlsplit(url)
        port = parsed.port  # Accessing this validates malformed ports.
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid URL: {exc}"
        ) from exc

    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only absolute HTTP and HTTPS URLs are supported"
        )
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="URLs containing embedded credentials are not supported"
        )
    if parsed.fragment:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="URL fragments are not supported"
        )
    if port is not None and not 1 <= port <= 65535:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="URL port is outside the valid range"
        )

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Localhost download URLs are not allowed"
        )
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Non-public IP address download URLs are not allowed"
        )

    return url


def _download_archive_filename(url: str, requested_filename: Optional[str]) -> str:
    """Choose a safe archive filename from an explicit value or URL path."""
    if requested_filename is not None and requested_filename.strip():
        filename = requested_filename
    else:
        filename = posixpath.basename(unquote(urlsplit(url).path))
        if not filename:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="filename is required when the URL path has no filename"
            )

    filename = _validate_direct_child_name(filename, "filename")
    if SSHManager.archive_type_from_path(filename) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Unsupported archive filename. Supported formats: .zip, .7z, .tar, "
                ".tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .txz, .gz, .bz2"
            )
        )
    return filename


def _cleanup_temp_file(path: str) -> None:
    """Remove a temporary file after FileResponse finishes sending it."""
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        logger.warning("Failed to clean up temporary download file: %s", path, exc_info=True)


def _download_headers(filename: str, file_size: Optional[int] = None) -> Dict[str, str]:
    """Build attachment headers with UTF-8 filename support."""
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii") or "download"
    ascii_filename = ascii_filename.replace("\\", "_").replace('"', "_")
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_filename}"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
    }
    if file_size is not None:
        headers["Content-Length"] = str(file_size)
    return headers


@router.get("", response_model=DirectoryListResponse)
async def list_directory(
    server_id: int,
    path: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
            detail="Access denied: path is outside server directory"
        )
    
    # List directory using SSH
    ssh_manager = SSHManager()
    success, files, error = await ssh_manager.list_directory(path, server)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error
        )
    
    return DirectoryListResponse(path=path, files=files)


@router.get("/content")
async def get_file_content(
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get file content for viewing/editing"""
    server = await get_server_for_user(server_id, db, current_user)
    
    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory"
        )
    
    # Read file using SSH
    ssh_manager = SSHManager()
    success, content, error = await ssh_manager.read_file(path, server)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error
        )
    
    return {"path": path, "content": content}


@router.put("/content")
async def update_file_content(
    server_id: int,
    path: str,
    request: FileContentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update file content"""
    server = await get_server_for_user(server_id, db, current_user)
    
    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory"
        )
    
    # Write file using SSH
    ssh_manager = SSHManager()
    success, error = await ssh_manager.write_file(path, request.content, server)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error
        )
    
    return {"success": True, "message": "File updated successfully"}


@router.post("/upload")
async def upload_file(
    server_id: int,
    path: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Upload file to server"""
    server = await get_server_for_user(server_id, db, current_user)
    
    # Construct remote path
    remote_path = remote_join(path, file.filename)
    
    # Security check
    if not is_path_safe(server.game_directory, remote_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory"
        )
    
    # Save uploaded file to temp location
    temp_file = None
    try:
        # Create temporary file
        temp_fd, temp_path = tempfile.mkstemp()
        os.close(temp_fd)
        temp_file = temp_path
        
        # Write uploaded content to temp file
        with open(temp_path, 'wb') as f:
            content = await file.read()
            f.write(content)
        
        # Upload to server using SSH
        ssh_manager = SSHManager()
        success, error = await ssh_manager.upload_file(temp_path, remote_path, server)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error
            )
        
        return {
            "success": True,
            "message": "File uploaded successfully",
            "path": remote_path,
            "filename": file.filename
        }
    
    finally:
        # Clean up temp file
        if temp_file and os.path.exists(temp_file):
            os.unlink(temp_file)


@router.get("/download")
async def download_file(
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user_for_download)
):
    """Download file from server"""
    server = await get_server_for_user(server_id, db, current_user)
    
    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory"
        )
    
    filename = posixpath.basename(path) or "download"
    ssh_manager = SSHManager()

    try:
        size_success, file_size, size_error = await ssh_manager.get_file_size(path, server)
        if not size_success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=size_error
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
                headers=_download_headers(filename, file_size)
            )

        temp_fd, temp_path = tempfile.mkstemp()
        os.close(temp_fd)

        success, error = await ssh_manager.download_file(path, temp_path, server)
        if not success:
            _cleanup_temp_file(temp_path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error
            )

        await ssh_manager.disconnect()

        return FileResponse(
            path=temp_path,
            filename=filename,
            media_type="application/octet-stream",
            headers=_download_headers(filename, file_size),
            background=BackgroundTask(_cleanup_temp_file, temp_path)
        )

    except HTTPException:
        await ssh_manager.disconnect()
        raise
    except Exception as e:
        await ssh_manager.disconnect()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error downloading file: {str(e)}"
        )


@router.post("/download-ticket")
async def create_download_ticket(
    server_id: int,
    request: DownloadTicketRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a short-lived one-time ticket for browser-native downloads."""
    server = await get_server_for_user(server_id, db, current_user)

    if not is_path_safe(server.game_directory, request.path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory"
        )

    ticket = await _create_download_ticket(current_user.id, server_id, request.path)
    return {"ticket": ticket, "expires_in": DOWNLOAD_TICKET_TTL_SECONDS}


async def _run_download_url_task(
    task_id: str,
    url: str,
    target_path: str,
    server: Server,
    overwrite: bool,
):
    """Download an archive on the SSH host without retaining its URL in status."""
    ssh_manager: Optional[SSHManager] = None
    try:
        async with download_url_tasks_lock:
            download_url_tasks[task_id]["status"] = "running"
            download_url_tasks[task_id]["started_at"] = time.time()

        logger.info("[URL Download] Starting task %s -> %s", task_id, target_path)
        ssh_manager = SSHManager()
        success, error = await ssh_manager.download_url_to_file(
            url,
            target_path,
            server,
            overwrite=overwrite,
        )

        async with download_url_tasks_lock:
            if success:
                download_url_tasks[task_id]["status"] = "completed"
                download_url_tasks[task_id]["message"] = "Archive downloaded successfully"
            else:
                download_url_tasks[task_id]["status"] = "failed"
                download_url_tasks[task_id]["error"] = error
            download_url_tasks[task_id]["completed_at"] = time.time()
    except Exception as exc:
        logger.exception("[URL Download] Task %s failed", task_id)
        async with download_url_tasks_lock:
            if task_id in download_url_tasks:
                download_url_tasks[task_id]["status"] = "failed"
                download_url_tasks[task_id]["error"] = str(exc)
                download_url_tasks[task_id]["completed_at"] = time.time()
    finally:
        if ssh_manager is not None:
            try:
                await ssh_manager.disconnect()
            except Exception:
                logger.warning(
                    "[URL Download] Failed to release SSH connection for task %s",
                    task_id,
                    exc_info=True,
                )
        async with download_url_tasks_lock:
            _download_url_task_refs.pop(task_id, None)


async def _cleanup_old_download_url_tasks():
    """Remove expired URL download task records and completed task references."""
    current_time = time.time()
    tasks_to_remove = []
    async with download_url_tasks_lock:
        for task_id, task_info in download_url_tasks.items():
            completed_at = task_info.get("completed_at")
            created_at = task_info.get("created_at")
            if completed_at and current_time - completed_at > EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS:
                tasks_to_remove.append(task_id)
            elif not completed_at and created_at and current_time - created_at > EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS:
                tasks_to_remove.append(task_id)

        for task_id in tasks_to_remove:
            download_url_tasks.pop(task_id, None)
            _download_url_task_refs.pop(task_id, None)


@router.post("/download-url", status_code=status.HTTP_202_ACCEPTED)
async def download_archive_from_url(
    server_id: int,
    request: DownloadUrlRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Start downloading an HTTP(S) archive directly on the SSH host."""
    server = await get_server_for_user(server_id, db, current_user)
    url = _validate_download_url(request.url)

    if not request.destination_path or not request.destination_path.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="destination_path cannot be empty",
        )
    destination_path = posixpath.normpath(request.destination_path)
    filename = _download_archive_filename(url, request.filename)
    target_path = remote_join(destination_path, filename)

    if not is_path_safe(server.game_directory, destination_path) or not is_path_safe(
        server.game_directory, target_path
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
            target_path,
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

    task = asyncio.create_task(
        _run_download_url_task(task_id, url, target_path, server, request.overwrite)
    )
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


@router.post("/mkdir")
async def create_directory(
    server_id: int,
    path: str,
    request: CreateDirectoryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
            detail="Access denied: path is outside server directory"
        )
    
    # Create directory using SSH
    ssh_manager = SSHManager()
    try:
        success, error = await ssh_manager.create_directory(new_dir_path, server)
    finally:
        await ssh_manager.disconnect()
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error
        )
    
    return {"success": True, "message": "Directory created successfully", "path": new_dir_path}


@router.delete("")
async def delete_path(
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete file or directory"""
    server = await get_server_for_user(server_id, db, current_user)
    
    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory"
        )
    
    # Don't allow deleting the root game directory
    if posixpath.normpath(path) == posixpath.normpath(server.game_directory):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete server root directory"
        )
    
    # Delete using SSH
    ssh_manager = SSHManager()
    success, error = await ssh_manager.delete_path(path, server)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error
        )
    
    return {"success": True, "message": "Deleted successfully"}


@router.post("/rename")
async def rename_file_or_directory(
    server_id: int,
    path: str,
    request: RenameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
            detail="Access denied: source path is outside server directory"
        )
    
    if not is_path_safe(server.game_directory, new_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination path is outside server directory"
        )
    
    # Don't allow renaming the root game directory
    if posixpath.normpath(old_path) == posixpath.normpath(server.game_directory):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot rename server root directory"
        )
    
    # Rename using SSH
    ssh_manager = SSHManager()
    success, error = await ssh_manager.rename_path(old_path, new_path, server)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error
        )
    
    return {"success": True, "message": "Renamed successfully", "new_path": new_path}


@router.post("/archive/inspect")
async def inspect_archive(
    server_id: int,
    request: InspectArchiveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Inspect a remote archive and return its safe directory entries."""
    server = await get_server_for_user(server_id, db, current_user)
    archive_path = posixpath.normpath(request.archive_path)
    if not is_path_safe(server.game_directory, archive_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: archive path is outside server directory",
        )

    ssh_manager = SSHManager()
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
        async with extraction_tasks_lock:
            extraction_tasks[task_id]["status"] = "running"
            extraction_tasks[task_id]["started_at"] = time.time()
        
        logger.info(f"[Extraction] Starting extraction task {task_id}: {archive_path} -> {destination_path}")
        
        # Extract using SSH
        ssh_manager = SSHManager()
        success, error = await ssh_manager.extract_archive(
            archive_path,
            destination_path,
            server,
            overwrite,
            source_folder=source_folder,
            strip_source_folder=strip_source_folder,
        )
        
        async with extraction_tasks_lock:
            if success:
                extraction_tasks[task_id]["status"] = "completed"
                extraction_tasks[task_id]["message"] = "Archive extracted successfully"
                logger.info(f"[Extraction] Task {task_id} completed successfully")
            else:
                extraction_tasks[task_id]["status"] = "failed"
                extraction_tasks[task_id]["error"] = error
                logger.error(f"[Extraction] Task {task_id} failed: {error}")
            
            extraction_tasks[task_id]["completed_at"] = time.time()
        
    except Exception as e:
        logger.exception(f"[Extraction] Task {task_id} encountered an exception")
        async with extraction_tasks_lock:
            extraction_tasks[task_id]["status"] = "failed"
            extraction_tasks[task_id]["error"] = str(e)
            extraction_tasks[task_id]["completed_at"] = time.time()
    finally:
        if ssh_manager is not None:
            try:
                await ssh_manager.disconnect()
            except Exception:
                logger.warning(
                    "[Extraction] Failed to release SSH connection for task %s",
                    task_id,
                    exc_info=True,
                )
        # Clean up task reference
        async with extraction_tasks_lock:
            if task_id in _extraction_task_refs:
                del _extraction_task_refs[task_id]


async def _cleanup_old_extraction_tasks():
    """Clean up extraction tasks older than configured thresholds"""
    current_time = time.time()
    tasks_to_remove = []
    
    async with extraction_tasks_lock:
        for task_id, task_info in extraction_tasks.items():
            # Remove completed/failed tasks older than threshold
            if task_info.get("completed_at"):
                if current_time - task_info["completed_at"] > EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS:
                    tasks_to_remove.append(task_id)
            # Remove pending tasks older than threshold (likely abandoned)
            elif task_info.get("created_at"):
                if current_time - task_info["created_at"] > EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS:
                    tasks_to_remove.append(task_id)
        
        for task_id in tasks_to_remove:
            del extraction_tasks[task_id]
            # Also clean up task reference if it exists
            if task_id in _extraction_task_refs:
                del _extraction_task_refs[task_id]


@router.post("/extract")
async def extract_archive(
    server_id: int,
    request: ExtractArchiveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Extract archive file (zip, tar, tar.gz, etc.) asynchronously.
    
    Returns immediately with a task_id that can be used to poll for status.
    The extraction runs in the background so the web UI doesn't block.
    """
    server = await get_server_for_user(server_id, db, current_user)
    
    archive_path = posixpath.normpath(request.archive_path)
    source_folder = _normalize_source_folder(request.source_folder)
    # If no destination specified or empty string, extract to the same directory as the archive
    if not request.destination_path or request.destination_path.strip() == '':
        destination_path = posixpath.dirname(archive_path)
    else:
        destination_path = posixpath.normpath(request.destination_path)
    
    # Security check
    if not is_path_safe(server.game_directory, archive_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: archive path is outside server directory"
        )
    
    if not is_path_safe(server.game_directory, destination_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination path is outside server directory"
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
            "error": None
        }
    
    # Start extraction task in background and store reference
    task = asyncio.create_task(_run_extraction_task(
        task_id,
        archive_path,
        destination_path,
        server,
        request.overwrite,
        source_folder,
        bool(request.strip_source_folder and source_folder),
    ))
    
    # Store task reference for proper cleanup/tracking
    async with extraction_tasks_lock:
        _extraction_task_refs[task_id] = task
    
    logger.info(f"[Extraction] Created task {task_id} for archive {archive_path}")
    
    return {
        "success": True,
        "task_id": task_id,
        "message": "Extraction started",
        "status": "pending",
        "destination": destination_path
    }


@router.get("/extract/status/{task_id}")
async def get_extraction_status(
    server_id: int,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
                detail="Extraction task not found or has expired"
            )
        
        task_info = extraction_tasks[task_id].copy()  # Copy to avoid holding lock during response
    
    # Verify the task belongs to this server and user
    if task_info.get("server_id") != server_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Task does not belong to this server"
        )
    
    if task_info.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Task does not belong to this user"
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
        "elapsed_seconds": elapsed
    }
