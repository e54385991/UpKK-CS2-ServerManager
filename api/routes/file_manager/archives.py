"""File Manager archives endpoints."""

# ruff: noqa: F403,F405

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.v1.operation_locks import reject_stuck_lock_unless_active
from api.routes.v1.operation_runner import enqueue_extract_archive
from services.server_operation_hub import ServerOperationConflict, server_operation_hub

from .common import *

router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])


@router.post("/archive/inspect")
async def inspect_archive(
    server_id: int,
    request: InspectArchiveRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
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


@router.post("/extract")
async def extract_archive(
    server_id: int,
    request: ExtractArchiveRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """
    Extract archive file (zip, tar, tar.gz, etc.) asynchronously.

    Returns immediately with a task_id that can be used to poll for status.
    The extraction runs in the background so the web UI doesn't block.
    """
    server = await get_server_for_user(server_id, db, current_user)

    archive_path, destination_path = resolve_extract_paths(
        server, request.archive_path, request.destination_path
    )
    source_folder = _normalize_source_folder(request.source_folder)

    await reject_stuck_lock_unless_active(server_id)
    try:
        record = await enqueue_extract_archive(
            server_id=server_id,
            actor_user_id=current_user.id,
            archive_path=archive_path,
            destination_path=destination_path,
            overwrite=request.overwrite,
            source_folder=source_folder,
            strip_source_folder=bool(request.strip_source_folder and source_folder),
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    logger.info(
        "[Extraction] Queued hub operation %s for archive %s", record["operation_id"], archive_path
    )
    return {
        "success": True,
        "task_id": record["operation_id"],
        "message": "Extraction started",
        "status": "pending",
        "destination": destination_path,
    }


@router.get("/extract/status/{task_id}")
async def get_extraction_status(
    server_id: int,
    task_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """
    Get the status of an extraction task.

    Returns the current status (pending, running, completed, failed) and any error message.
    """
    await get_server_for_user(server_id, db, current_user)
    record = await server_operation_hub.get(task_id)
    if record is None or int(record["server_id"]) != server_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Extraction task not found or has expired",
        )
    payload = file_task_payload_from_hub(record)
    payload["source_folder"] = None
    payload["strip_source_folder"] = False
    return payload
