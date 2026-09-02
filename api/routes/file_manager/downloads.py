"""File Manager downloads endpoints."""

# ruff: noqa: F403,F405

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.v1.operation_locks import reject_stuck_lock_unless_active
from api.routes.v1.operation_runner import enqueue_url_download
from services.server_operation_hub import ServerOperationConflict, server_operation_hub

from .common import *
from .common import _download_archive_filename, _validate_download_url

router = APIRouter(prefix="/servers/{server_id}/files", tags=["file-manager"])


@router.post("/download-url", status_code=status.HTTP_202_ACCEPTED)
async def download_archive_from_url(
    server_id: int,
    request: DownloadUrlRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Start downloading an HTTP(S) archive directly on the SSH host."""
    server = await get_server_for_user(server_id, db, current_user)
    url = _validate_download_url(request.url)

    if not request.destination_path or not request.destination_path.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="destination_path cannot be empty",
        )
    destination_path = posixpath.normpath(request.destination_path)
    filename = _download_archive_filename(
        url,
        request.filename,
        allow_unresolved=True,
    )
    target_path = remote_join(destination_path, filename) if filename else None

    if not is_path_safe(server.game_directory, destination_path) or (
        target_path is not None and not is_path_safe(server.game_directory, target_path)
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
            overwrite=request.overwrite,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return {
        "success": True,
        "task_id": record["operation_id"],
        "status": "pending",
        "target_path": target_path,
    }


@router.get("/download-url/status/{task_id}")
async def get_download_url_status(
    server_id: int,
    task_id: str,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Return status for a URL download task owned by this user and server."""
    await get_server_for_user(server_id, db, current_user)
    record = await server_operation_hub.get(task_id)
    if record is None or int(record["server_id"]) != server_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Download task not found or has expired",
        )
    return file_task_payload_from_hub(record)
