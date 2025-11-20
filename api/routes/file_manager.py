"""
File manager routes for server file operations
"""
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from pydantic import BaseModel
import os
import tempfile
import shutil

from modules import Server, get_db, User, get_current_active_user
from services import SSHManager

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


class RenameRequest(BaseModel):
    """Rename/move file request"""
    new_name: str


class DeleteRequest(BaseModel):
    """Delete file/directory request"""
    path: str


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
async def rename_path(
    server_id: int,
    path: str,
    request: RenameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Rename or move file/directory"""
    server = await get_server_for_user(server_id, db, current_user)
    
    # Construct new path
    parent_dir = os.path.dirname(path)
    new_path = os.path.join(parent_dir, request.new_name)
    
    # Security checks
    if not is_path_safe(server.game_directory, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: source path is outside server directory"
        )
    
    if not is_path_safe(server.game_directory, new_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination path is outside server directory"
        )
    
    # Rename using SSH
    ssh_manager = SSHManager()
    success, error = await ssh_manager.rename_path(path, new_path, server)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error
        )
    
    return {"success": True, "message": "Renamed successfully", "new_path": new_path}
