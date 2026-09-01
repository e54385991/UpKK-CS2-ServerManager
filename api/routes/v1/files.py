"""Versioned file-manager workspace for the Next.js console."""

from __future__ import annotations

import posixpath

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from api.routes.file_manager import archives as legacy_archives
from api.routes.file_manager import files as legacy_files
from api.routes.file_manager.common import (
    CopyPathsRequest,
    CreateDirectoryRequest,
    DownloadTicketRequest,
    DownloadUser,
    FileContentRequest,
    InspectArchiveRequest,
    RenameRequest,
    SSHManager,
    _download_archive_filename,
    _normalize_source_folder,
    _validate_download_url,
    file_task_payload_from_hub,
    is_path_safe,
    remote_join,
    resolve_extract_paths,
)
from api.routes.v1.operation_locks import reject_stuck_lock_unless_active
from api.routes.v1.operation_runner import enqueue_extract_archive, enqueue_url_download
from api.routes.v1.operations import to_view
from services.audit_log_service import record_audit_event
from services.server_operation_hub import ServerOperationConflict, server_operation_hub

from .schemas import (
    FileArchiveInspectRequest,
    FileArchiveInspectView,
    FileContentUpdateRequest,
    FileContentView,
    FileCopyRequest,
    FileDownloadTicketView,
    FileEntryView,
    FileExtractRequest,
    FileMkdirRequest,
    FileMutationResult,
    FileRenameRequest,
    FilesWorkspaceView,
    FileTaskView,
    FileUrlDownloadRequest,
    ServerOperationView,
)

router = APIRouter(prefix="/api/v1/servers/{server_id}/files", tags=["v1-files"])

_MISSING_PATH_MARKERS = (
    "no such file",
    "no such path",
    "not a directory",
    "does not exist",
)


def _is_missing_path_error(error: str | None) -> bool:
    text = (error or "").lower()
    return any(marker in text for marker in _MISSING_PATH_MARKERS)


def _rewrite_nested_cs2_game(path: str, root: str) -> str | None:
    """Try the other CS2 layout: ``<root>/cs2/game/…`` ↔ ``<root>/game/…``."""
    if "/cs2/game/" in path or path.endswith("/cs2/game"):
        alternate = path.replace("/cs2/game", "/game", 1)
    elif "/game/" in path or path.endswith("/game"):
        alternate = path.replace("/game", "/cs2/game", 1)
    else:
        return None
    if alternate == path or not is_path_safe(root, alternate):
        return None
    return alternate


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
        or (str(payload["destination"]) if payload.get("destination") else None)
        or (str(payload["destination_path"]) if payload.get("destination_path") else None),
        elapsed_seconds=float(payload["elapsed_seconds"])
        if payload.get("elapsed_seconds") is not None
        else None,
    )


async def _hub_file_task(server_id: int, task_id: str) -> FileTaskView:
    record = await server_operation_hub.get(task_id)
    if record is None or int(record["server_id"]) != server_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File task not found or has expired",
        )
    return _task(task_id, file_task_payload_from_hub(record))


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
        if not success and _is_missing_path_error(error):
            rewritten = _rewrite_nested_cs2_game(requested, root)
            if rewritten:
                success, files, error = await ssh.list_directory(rewritten, server)
                if success:
                    requested = rewritten
    finally:
        await ssh.disconnect()
    if not success:
        if _is_missing_path_error(error):
            return FilesWorkspaceView(
                server_id=server_id,
                root=root,
                path=requested,
                ssh_ok=True,
                files=[],
                message=error or "No such file",
            )
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
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileMutationResult:
    payload = await legacy_files.update_file_content(
        server_id,
        path,
        FileContentRequest(content=body.content),
        db,
        current_user,
        request,
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
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
    file: UploadFile = File(...),
    relative_path: str | None = Query(default=None, max_length=1500),
) -> FileMutationResult:
    payload = await legacy_files.upload_file(
        server_id,
        path,
        file,
        db,
        current_user,
        relative_path,
        request,
    )
    return FileMutationResult(
        success=bool(payload.get("success", True)),
        message=str(payload.get("message") or "File uploaded successfully"),
        path=str(payload.get("path") or ""),
    )


@router.post("/copy", response_model=FileMutationResult)
async def copy_paths(
    server_id: int,
    body: FileCopyRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileMutationResult:
    payload = await legacy_files.copy_paths(
        server_id,
        CopyPathsRequest(sources=list(body.sources), destination=body.destination),
        db,
        current_user,
        request,
    )
    return FileMutationResult(
        success=bool(payload.get("success", True)),
        message=str(payload.get("message") or "Copied successfully"),
        path=str(payload.get("path") or ""),
        paths=[str(item) for item in payload.get("paths") or []],
    )


@router.get("/download", response_model=None)
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
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileMutationResult:
    payload = await legacy_files.create_directory(
        server_id,
        path,
        CreateDirectoryRequest(name=body.name),
        db,
        current_user,
        request,
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
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileMutationResult:
    payload = await legacy_files.delete_path(server_id, path, db, current_user, request)
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
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileMutationResult:
    payload = await legacy_files.rename_file_or_directory(
        server_id,
        path,
        RenameRequest(old_name=body.old_name, new_name=body.new_name),
        db,
        current_user,
        request,
    )
    return FileMutationResult(
        success=bool(payload.get("success", True)),
        message=str(payload.get("message") or "Renamed successfully"),
        path=str(payload.get("new_path") or ""),
    )


@router.post(
    "/download-url", response_model=ServerOperationView, status_code=status.HTTP_202_ACCEPTED
)
async def download_from_url(
    server_id: int,
    body: FileUrlDownloadRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    server = await require_server_access(db, server_id, current_user)
    url = _validate_download_url(body.url)
    if not body.destination_path or not body.destination_path.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="destination_path cannot be empty",
        )
    destination_path = posixpath.normpath(body.destination_path)
    filename = _download_archive_filename(url, body.filename, allow_unresolved=True)
    target_path = remote_join(destination_path, filename) if filename else None
    if not is_path_safe(server.game_directory, destination_path) or (
        target_path is not None and not is_path_safe(server.game_directory, target_path)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination path is outside server directory",
        )
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
    await reject_stuck_lock_unless_active(server_id)
    try:
        record = await enqueue_url_download(
            server_id=server_id,
            actor_user_id=current_user.id,
            url=url,
            destination_path=destination_path,
            target_path=target_path,
            overwrite=body.overwrite,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit_event(
        category="files",
        action="files.download_url",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "operation_id": record["operation_id"],
            "destination": destination_path,
            "target_path": target_path,
        },
    )
    return to_view(record)


@router.get("/download-url/{task_id}", response_model=FileTaskView)
async def get_download_url_status(
    server_id: int,
    task_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileTaskView:
    await require_server_access(db, server_id, current_user)
    return await _hub_file_task(server_id, task_id)


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


@router.post(
    "/archives/extract", response_model=ServerOperationView, status_code=status.HTTP_202_ACCEPTED
)
async def extract_archive(
    server_id: int,
    body: FileExtractRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    server = await require_server_access(db, server_id, current_user)
    archive_path, destination_path = resolve_extract_paths(
        server, body.archive_path, body.destination_path
    )
    source_folder = _normalize_source_folder(body.source_folder)
    await reject_stuck_lock_unless_active(server_id)
    try:
        record = await enqueue_extract_archive(
            server_id=server_id,
            actor_user_id=current_user.id,
            archive_path=archive_path,
            destination_path=destination_path,
            overwrite=body.overwrite,
            source_folder=source_folder,
            strip_source_folder=bool(body.strip_source_folder and source_folder),
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit_event(
        category="files",
        action="files.extract",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "operation_id": record["operation_id"],
            "path": archive_path,
            "destination": destination_path,
            "source_folder": source_folder,
        },
    )
    return to_view(record)


@router.get("/archives/extract/{task_id}", response_model=FileTaskView)
async def get_extract_status(
    server_id: int,
    task_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> FileTaskView:
    await require_server_access(db, server_id, current_user)
    return await _hub_file_task(server_id, task_id)
