"""
File manager routes for server file operations
"""
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import os
import tempfile
import shutil
import asyncio
import uuid
import time
import logging

from modules import Server, get_db, User, get_current_active_user
from services import SSHManager

logger = logging.getLogger(__name__)

# Constants for extraction task cleanup
EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS = 3600  # 1 hour
EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS = 7200  # 2 hours

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

router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])


class FileInfo(BaseModel):
    """File information model"""
    name: str
    path: str
    type: str
    size: int
    modified: float
    permissions: str
    is_symlink: bool


class DirectoryListResponse(BaseModel):
    """Directory listing response"""
    path: str
    files: List[FileInfo]


class FileContentRequest(BaseModel):
    """File content update request"""
    content: str


class CreateDirectoryRequest(BaseModel):
    """Create directory request"""
    name: str


class DeleteRequest(BaseModel):
    """Delete file/directory request"""
    path: str


class RenameRequest(BaseModel):
    """Rename file/directory request"""
    old_name: str
    new_name: str


class ExtractArchiveRequest(BaseModel):
    """Extract archive request"""
    archive_path: str
    destination_path: Optional[str] = None
    overwrite: bool = False


async def get_server_for_user(server_id: int, db: AsyncSession, current_user: User) -> Server:
    """Helper to get server and verify ownership"""
    result = await db.execute(select(Server).filter(Server.id == server_id))
    server = result.scalar_one_or_none()
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server with ID {server_id} not found"
        )
    
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this server"
        )
    
    return server


def is_path_safe(base_path: str, requested_path: str) -> bool:
    """
    Verify that the requested path is within the server's directory
    Prevents path traversal attacks
    """
    # Normalize paths
    base = os.path.normpath(base_path)
    requested = os.path.normpath(requested_path)
    
    # Check if requested path starts with base path
    return requested.startswith(base)


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
    remote_path = os.path.join(path, file.filename)
    
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
    current_user: User = Depends(get_current_active_user)
):
    """Download file from server"""
    server = await get_server_for_user(server_id, db, current_user)
    
    # Security check
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory"
        )
    
    # Download to temp location
    temp_file = None
    try:
        # Create temporary file
        temp_fd, temp_path = tempfile.mkstemp()
        os.close(temp_fd)
        temp_file = temp_path
        
        # Download from server using SSH
        ssh_manager = SSHManager()
        success, error = await ssh_manager.download_file(path, temp_path, server)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error
            )
        
        # Get filename for download
        filename = os.path.basename(path)
        
        # Return file as download
        return FileResponse(
            path=temp_path,
            filename=filename,
            media_type='application/octet-stream',
            background=None  # Don't delete temp file yet
        )
    
    except Exception as e:
        # Clean up on error
        if temp_file and os.path.exists(temp_file):
            os.unlink(temp_file)
        raise


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
    
    # Construct full path
    new_dir_path = os.path.join(path, request.name)
    
    # Security check
    if not is_path_safe(server.game_directory, new_dir_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory"
        )
    
    # Create directory using SSH
    ssh_manager = SSHManager()
    success, error = await ssh_manager.create_directory(new_dir_path, server)
    
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
    if os.path.normpath(path) == os.path.normpath(server.game_directory):
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
    old_path = os.path.join(path, request.old_name)
    new_path = os.path.join(path, request.new_name)
    
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
    if os.path.normpath(old_path) == os.path.normpath(server.game_directory):
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


async def _run_extraction_task(
    task_id: str,
    archive_path: str,
    destination_path: str,
    server: Server,
    overwrite: bool
):
    """Background task to perform archive extraction"""
    try:
        async with extraction_tasks_lock:
            extraction_tasks[task_id]["status"] = "running"
            extraction_tasks[task_id]["started_at"] = time.time()
        
        logger.info(f"[Extraction] Starting extraction task {task_id}: {archive_path} -> {destination_path}")
        
        # Extract using SSH
        ssh_manager = SSHManager()
        success, error = await ssh_manager.extract_archive(
            archive_path, destination_path, server, overwrite
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
    
    archive_path = request.archive_path
    # If no destination specified or empty string, extract to the same directory as the archive
    if not request.destination_path or request.destination_path.strip() == '':
        destination_path = os.path.dirname(archive_path)
    else:
        destination_path = request.destination_path
    
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
        task_id, archive_path, destination_path, server, request.overwrite
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
        "message": task_info.get("message"),
        "error": task_info.get("error"),
        "elapsed_seconds": elapsed
    }
