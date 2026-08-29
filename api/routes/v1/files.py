"""Versioned file-manager workspace for the Next.js console."""

from __future__ import annotations

import posixpath

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from api.routes.file_manager import archives as legacy_archives
from api.routes.file_manager import downloads as legacy_downloads
from api.routes.file_manager import files as legacy_files
from api.routes.file_manager.common import (
    CreateDirectoryRequest,
    DownloadTicketRequest,
    DownloadUrlRequest,
    DownloadUser,
    ExtractArchiveRequest,
    FileContentRequest,
    InspectArchiveRequest,
    RenameRequest,
    SSHManager,
    is_path_safe,
)

from .schemas import (
    FileArchiveInspectRequest,
    FileArchiveInspectView,
    FileContentUpdateRequest,
    FileContentView,
    FileDownloadTicketView,
    FileEntryView,
    FileExtractRequest,
    FileMkdirRequest,
    FileMutationResult,
    FileRenameRequest,
    FilesWorkspaceView,
    FileTaskView,
    FileUrlDownloadRequest,
)

router = APIRouter(prefix="/api/v1/servers/{server_id}/files", tags=["v1-files"])


def _entry(raw: dict[str, object]) -> FileEntryView:
    kind = raw.get("type")
    return FileEntryView(
        name=str(raw.get("name") or ""),
        path=str(raw.get("path") or ""),
        type="directory" if kind == "directory" else "file",
        size=int(raw.get("size") or 0),
        modified=float(raw.get("modified") or 0),
        permissions=str(raw.get("permissions") or "000"),
        is_symlink=bool(raw.get("is_symlink", False)),
    )


def _task(
    task_id: str,
    payload: dict[str, object],
    *,
    destination: str | None = None,
) -> FileTaskView:
    return FileTaskView(
        task_id=task_id,
        status=str(payload.get("status") or "pending"),
        message=str(payload["message"]) if payload.get("message") else None,
        error=str(payload["error"]) if payload.get("error") else None,
        target_path=str(payload["target_path"]) if payload.get("target_path") else None,
        destination=destination
        or (str(payload["destination"]) if payload.get("destination") else None),
        elapsed_seconds=float(payload["elapsed_seconds"])
        if payload.get("elapsed_seconds") is not None
        else None,
    )


@router.get("", response_model=FilesWorkspaceView)
async def get_files_workspace(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    path: str | None = Query(default=None),
) -> FilesWorkspaceView:
    """List a directory. SSH failures stay 200 so the category page can render."""
    server = await require_server_access(db, server_id, current_user)
    root = server.game_directory
    requested = posixpath.normpath(path.strip()) if path and path.strip() else root
    if not is_path_safe(root, requested):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside server directory",
        )
    ssh = SSHManager()
    try:
        success, files, error = await ssh.list_directory(requested, server)
    finally:
        await ssh.disconnect()
    if not success:
        return FilesWorkspaceView(
            server_id=server_id,
            root=root,
            path=requested,
            ssh_ok=False,
            ssh_error=error or "SSH connection failed",
            files=[],
        )
    return FilesWorkspaceView(
        server_id=server_id,
        root=root,
        path=requested,
        ssh_ok=True,
        files=[_entry(item) for item in files if isinstance(item, dict)],
    )


@router.get("/content", response_model=FileContentView)
async def get_file_content(
    server_id: int,
    path: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileContentView:
    payload = await legacy_files.get_file_content(server_id, path, db, current_user)
    return FileContentView(path=str(payload["path"]), content=str(payload.get("content") or ""))


@router.put("/content", response_model=FileMutationResult)
async def update_file_content(
    server_id: int,
    path: str,
    body: FileContentUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileMutationResult:
    payload = await legacy_files.update_file_content(
        server_id,
        path,
        FileContentRequest(content=body.content),
        db,
        current_user,
    )
    return FileMutationResult(
        success=bool(payload.get("success", True)),
        message=str(payload.get("message") or "File updated successfully"),
        path=path,
    )


@router.post("/upload", response_model=FileMutationResult)
async def upload_file(
    server_id: int,
    path: str,
    db: DatabaseSession,
    current_user: ActiveUser,
    file: UploadFile = File(...),
) -> FileMutationResult:
    payload = await legacy_files.upload_file(server_id, path, file, db, current_user)
    return FileMutationResult(
        success=bool(payload.get("success", True)),
        message=str(payload.get("message") or "File uploaded successfully"),
        path=str(payload.get("path") or ""),
    )


@router.get("/download")
async def download_file(
    server_id: int,
    path: str,
    db: DatabaseSession,
    current_user: DownloadUser,
):
    return await legacy_files.download_file(server_id, path, db, current_user)


@router.post("/download-ticket", response_model=FileDownloadTicketView)
async def create_download_ticket(
    server_id: int,
    body: DownloadTicketRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileDownloadTicketView:
    payload = await legacy_files.create_download_ticket(server_id, body, db, current_user)
    return FileDownloadTicketView(
        ticket=str(payload["ticket"]),
        expires_in=int(payload.get("expires_in") or 60),
        path=body.path,
    )


@router.post("/mkdir", response_model=FileMutationResult)
async def create_directory(
    server_id: int,
    path: str,
    body: FileMkdirRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileMutationResult:
    payload = await legacy_files.create_directory(
        server_id,
        path,
        CreateDirectoryRequest(name=body.name),
        db,
        current_user,
    )
    return FileMutationResult(
        success=bool(payload.get("success", True)),
        message=str(payload.get("message") or "Directory created successfully"),
        path=str(payload.get("path") or ""),
    )


@router.delete("", response_model=FileMutationResult)
async def delete_path(
    server_id: int,
    path: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileMutationResult:
    payload = await legacy_files.delete_path(server_id, path, db, current_user)
    return FileMutationResult(
        success=bool(payload.get("success", True)),
        message=str(payload.get("message") or "Deleted successfully"),
        path=path,
    )


@router.post("/rename", response_model=FileMutationResult)
async def rename_path(
    server_id: int,
    path: str,
    body: FileRenameRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileMutationResult:
    payload = await legacy_files.rename_file_or_directory(
        server_id,
        path,
        RenameRequest(old_name=body.old_name, new_name=body.new_name),
        db,
        current_user,
    )
    return FileMutationResult(
        success=bool(payload.get("success", True)),
        message=str(payload.get("message") or "Renamed successfully"),
        path=str(payload.get("new_path") or ""),
    )


@router.post("/download-url", response_model=FileTaskView, status_code=status.HTTP_202_ACCEPTED)
async def download_from_url(
    server_id: int,
    body: FileUrlDownloadRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileTaskView:
    payload = await legacy_downloads.download_archive_from_url(
        server_id,
        DownloadUrlRequest(**body.model_dump()),
        db,
        current_user,
    )
    return _task(str(payload["task_id"]), payload)


@router.get("/download-url/{task_id}", response_model=FileTaskView)
async def get_download_url_status(
    server_id: int,
    task_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileTaskView:
    payload = await legacy_downloads.get_download_url_status(server_id, task_id, db, current_user)
    return _task(task_id, payload)


@router.post("/archives/inspect", response_model=FileArchiveInspectView)
async def inspect_archive(
    server_id: int,
    body: FileArchiveInspectRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileArchiveInspectView:
    payload = await legacy_archives.inspect_archive(
        server_id,
        InspectArchiveRequest(archive_path=body.archive_path),
        db,
        current_user,
    )
    return FileArchiveInspectView(
        archive_type=str(payload.get("archive_type") or ""),
        folders=[str(item) for item in payload.get("folders") or []],
        entry_count=int(payload.get("entry_count") or 0),
    )


@router.post("/archives/extract", response_model=FileTaskView, status_code=status.HTTP_202_ACCEPTED)
async def extract_archive(
    server_id: int,
    body: FileExtractRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileTaskView:
    payload = await legacy_archives.extract_archive(
        server_id,
        ExtractArchiveRequest(**body.model_dump()),
        db,
        current_user,
    )
    return _task(
        str(payload["task_id"]),
        payload,
        destination=str(payload.get("destination") or ""),
    )


@router.get("/archives/extract/{task_id}", response_model=FileTaskView)
async def get_extract_status(
    server_id: int,
    task_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileTaskView:
    payload = await legacy_archives.get_extraction_status(server_id, task_id, db, current_user)
    return _task(task_id, payload)
