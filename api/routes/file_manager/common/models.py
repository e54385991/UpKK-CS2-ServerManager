"""File-manager request and listing models."""

from __future__ import annotations

from typing import List, Optional

from sqlmodel import SQLModel


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


class CopyPathsRequest(SQLModel):
    """Copy one or more remote paths into a destination directory."""

    sources: List[str]
    destination: str


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
