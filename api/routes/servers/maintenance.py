"""Servers maintenance endpoints."""

# ruff: noqa: F403,F405

import logging
from typing import Any, cast

from fastapi import Request

from api.dependencies import (
    SSHManagerProvider,
    get_unit_of_work,
    resolve_s3_backup_service,
)
from api.response_models import (
    AllServersDiskSpaceResponse,
    DeploymentConfirmationResponse,
    SSHHealthResponse,
    SSHReconnectResponse,
)
from cs2_manager.core import ErrorResponse, Principal
from cs2_manager.features.servers import (
    CPUCountResponse,
    CPUCountService,
    DeploymentCheckResponse,
    DeploymentCheckService,
    DiskSpaceResponse,
    DiskSpaceService,
    S3RestoreResponse,
    ServerSystemInfoNotFoundError,
    ServerSystemInfoRepository,
    cpu_count_response,
    deployment_check_response,
    disk_space_response,
)
from cs2_manager.infrastructure import UnitOfWork
from modules.auth import get_current_principal
from services.maintenance_lock import maintenance_lock_service
from services.s3_backup_service import S3BackupService

from .common import *

router = APIRouter(prefix="/servers", tags=["servers"])
logger = logging.getLogger(__name__)


def _uow_session(uow: UnitOfWork) -> AsyncSession:
    if uow.session is None:
        raise RuntimeError("Unit of work is not active")
    return uow.session


def _require_system_info_cache(request: Request) -> Any:
    """Resolve the current application's cache without a global fallback."""
    container = getattr(request.app.state, "container", None)
    cache = getattr(container, "redis", None)
    if cache is None or not all(
        callable(getattr(cache, method, None)) for method in ("get", "set")
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="System information cache is unavailable",
        )
    return cache


async def _require_system_info_target(
    uow: UnitOfWork,
    server_id: int,
    principal: Principal,
):
    repository = ServerSystemInfoRepository(_uow_session(uow))
    try:
        target = await repository.require_target(server_id, principal)
    except ServerSystemInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await uow.commit()
    return target


async def _release_ssh_manager(ssh_manager: SSHManager, operation: str) -> None:
    """Release an SSH manager without hiding the primary route result."""
    try:
        await ssh_manager.disconnect()
    except Exception:
        logger.warning("Failed to release SSH connection after %s", operation, exc_info=True)


@router.get(
    "/disk-space-all",
    response_model=AllServersDiskSpaceResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    },
)
async def get_all_servers_disk_space(
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get cached disk space information for all servers owned by current user.

    Args:
        force_refresh: If True, bypass cache and read from system

    NOTE: This route MUST be defined before /{server_id} routes
    to avoid path parameter matching conflicts.
    """
    from services.system_info_helper import system_info_helper

    # Get all servers for current user
    servers = await Server.get_all_by_user(db, cast(int, current_user.id))

    # Get disk space for all servers
    disk_space_map = await system_info_helper.get_all_servers_disk_space(
        servers, force_refresh=force_refresh
    )

    # Convert to string keys for JSON
    response = {str(k): v for k, v in disk_space_map.items()}

    return {"servers": response, "timestamp": get_current_time().isoformat()}


@router.get(
    "/{server_id}/cleanup/scan",
    response_model=CleanupScanResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
)
async def scan_server_cleanup(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Scan approved game directory cleanup candidates for this server"""
    server = await get_server_with_permission(server_id, current_user, db)
    ssh_manager = SSHManager()

    try:
        success, data, error = await game_cleanup_service.scan(ssh_manager, server)
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
        return data
    finally:
        await _release_ssh_manager(ssh_manager, "cleanup scan")


@router.post(
    "/{server_id}/cleanup/delete",
    response_model=CleanupDeleteResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def delete_server_cleanup_items(
    server_id: int,
    cleanup_data: CleanupDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
):
    """Delete approved game directory cleanup candidates for this server"""
    if not callable(getattr(lock_service, "get", None)):
        lock_service = maintenance_lock_service
    server = await get_server_with_permission(server_id, current_user, db)
    async with lock_service.get(
        server_id,
        operation="server_cleanup_delete",
        wait=False,
        ttl=7200,
    ):
        ssh_manager = SSHManager()
        try:
            success, result, error = await game_cleanup_service.delete(
                ssh_manager,
                server,
                cleanup_data.mode,
                paths=cleanup_data.paths,
                confirmation_text=cleanup_data.confirmation_text,
            )
            if error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
            return result
        finally:
            await _release_ssh_manager(ssh_manager, "cleanup deletion")


@router.get(
    "/{server_id}/s3-backups",
    response_model=List[S3BackupItem],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def list_server_s3_backups(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    s3_service: S3BackupService = Depends(resolve_s3_backup_service),
):
    """List S3 plugin backups for this server"""
    server = await get_server_with_permission(server_id, current_user, db)
    owner = await get_server_owner_user(db, server, current_user)
    detached_server = Server.model_validate(server, from_attributes=True)
    detached_owner = User.model_validate(owner, from_attributes=True)
    await db.commit()

    success, backups, error = await s3_service.list_backups(
        detached_owner,
        detached_server,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    return backups


@router.post(
    "/{server_id}/s3-restore",
    response_model=S3RestoreResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def restore_server_s3_backup(
    server_id: int,
    restore_data: S3RestoreRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    lock_service: MaintenanceLockService = Depends(resolve_maintenance_lock_service),
    s3_service: S3BackupService = Depends(resolve_s3_backup_service),
):
    """Restore a selected S3 plugin backup to this server"""
    if not callable(getattr(lock_service, "get", None)):
        lock_service = maintenance_lock_service
    server = await get_server_with_permission(server_id, current_user, db)
    owner = await get_server_owner_user(db, server, current_user)

    if not s3_service.validate_object_key(owner, server, restore_data.object_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Selected S3 backup does not belong to this server",
        )

    # Avoid carrying request-session ORM state through S3 and SSH operations.
    detached_server = Server.model_validate(server, from_attributes=True)
    detached_owner = User.model_validate(owner, from_attributes=True)
    await db.commit()

    async with lock_service.get(
        server_id,
        operation="s3_restore",
        wait=False,
        ttl=7200,
    ):
        temp_dir = tempfile.mkdtemp(prefix="cs2_s3_restore_")
        local_path = os.path.join(
            temp_dir,
            s3_service.safe_object_filename(restore_data.object_key),
        )
        ssh_manager = SSHManager()

        try:
            download_success, download_error = await s3_service.download_backup(
                detached_owner,
                detached_server,
                restore_data.object_key,
                local_path,
            )
            if not download_success:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=download_error)

            safety_success, safety_message = await ssh_manager.backup_plugins(detached_server)
            if not safety_success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to create safety backup before restore: {safety_message}",
                )

            game_dir = detached_server.game_directory.rstrip("/")
            filename = s3_service.safe_object_filename(restore_data.object_key)
            remote_restore_path = f"{game_dir}/backups/s3-restore-{uuid.uuid4().hex[:8]}-{filename}"

            upload_success, upload_error = await ssh_manager.upload_file(
                local_path,
                remote_restore_path,
                detached_server,
            )
            if not upload_success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to upload restore archive to server: {upload_error}",
                )

            csgo_dir = f"{game_dir}/cs2/game/csgo"
            extract_success, extract_error = await ssh_manager.extract_archive(
                remote_restore_path,
                csgo_dir,
                detached_server,
                overwrite=True,
            )
            if not extract_success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to extract restore archive: {extract_error}",
                )

            return {
                "success": True,
                "message": "S3 plugin backup restored successfully",
                "restored_from": restore_data.object_key,
                "remote_archive_path": remote_restore_path,
                "safety_backup": getattr(ssh_manager, "last_plugin_backup", None),
            }
        finally:
            await _release_ssh_manager(ssh_manager, "S3 restore")
            await to_thread.run_sync(lambda: shutil.rmtree(temp_dir, ignore_errors=True))


@router.get(
    "/{server_id}/cpu-count",
    response_model=CPUCountResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def get_server_cpu_count(
    server_id: int,
    ssh_manager: SSHManagerProvider,
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_current_principal),
):
    """Get CPU core count from the remote server"""
    target = await _require_system_info_target(uow, server_id, current_user)
    result = await CPUCountService(ssh_manager).get_cpu_count(target)
    return cpu_count_response(result)


@router.get(
    "/{server_id}/disk-space",
    response_model=DiskSpaceResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def get_server_disk_space(
    request: Request,
    server_id: int,
    ssh_manager: SSHManagerProvider,
    force_refresh: bool = False,
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_current_principal),
):
    """
    Get disk space information for server directory

    Args:
        force_refresh: If True, bypass cache and read from system
    """
    cache = _require_system_info_cache(request)
    target = await _require_system_info_target(uow, server_id, current_user)
    result = await DiskSpaceService(cache, ssh_manager).get_disk_space(
        target,
        force_refresh=force_refresh,
    )
    return disk_space_response(result)


@router.get(
    "/{server_id}/check-deployment",
    response_model=DeploymentCheckResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def check_server_deployment(
    server_id: int,
    ssh_manager: SSHManagerProvider,
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_current_principal),
):
    """
    Check if server is actually deployed by verifying cs2 binary file exists

    Returns:
        {
            "is_deployed": bool,
            "binary_path": str,
            "message": str
        }
    """
    target = await _require_system_info_target(uow, server_id, current_user)
    result = await DeploymentCheckService(ssh_manager).check(target)
    return deployment_check_response(result)


@router.post(
    "/{server_id}/confirm-deployment",
    response_model=DeploymentConfirmationResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def confirm_server_deployment(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Manually record a completed deployment when remote verification is unavailable."""
    server = await get_server_with_permission(server_id, current_user, db)

    if await redis_manager.get(f"deployment_lock:{server_id}"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A deployment is currently in progress and cannot be confirmed manually",
        )

    server.last_deployed = get_current_time()
    if server.status != ServerStatus.RUNNING:
        server.status = ServerStatus.STOPPED

    db.add(
        DeploymentLog(
            server_id=server_id,
            action="manual_deployment_confirmation",
            status="success",
            output="Deployment manually confirmed by the user",
        )
    )
    await db.commit()
    await db.refresh(server)

    await redis_manager.set_server_status(server_id, server.status.value)

    return {
        "success": True,
        "message": "Deployment marked as complete",
        "status": server.status.value,
        "last_deployed": server.last_deployed,
    }


@router.post(
    "/{server_id}/ssh-reconnect",
    response_model=SSHReconnectResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
    description=(
        "Manually reconnect to a server and reset SSH health status\n\n"
        'This endpoint is used to restore a "completely_down" server after \n'
        "manual intervention (e.g., fixing network issues, updating credentials)."
    ),
)
async def manual_ssh_reconnect(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Manually reconnect to a server and reset SSH health status

    This endpoint is used to restore a "completely_down" server after
    manual intervention (e.g., fixing network issues, updating credentials).
    """
    # Get server and verify ownership
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=403, detail="You don't have permission to access this server"
        )

    # Use SSH health monitor to perform manual reconnection
    from services.ssh_health_monitor import ssh_health_monitor

    success, message = await ssh_health_monitor.manual_reconnect(server_id)

    if success:
        return {"success": True, "message": message, "ssh_health_status": "healthy"}
    else:
        return {"success": False, "message": message, "ssh_health_status": server.ssh_health_status}


@router.get(
    "/{server_id}/ssh-health",
    response_model=SSHHealthResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
)
async def get_ssh_health_status(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get SSH health status for a server"""
    # Get server and verify ownership
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=403, detail="You don't have permission to access this server"
        )

    # Calculate offline duration estimate based on consecutive failures
    offline_duration_estimate = None
    if server.consecutive_ssh_failures > 0:
        check_interval_hours = server.ssh_health_check_interval_hours or 2
        offline_hours = server.consecutive_ssh_failures * check_interval_hours
        offline_duration_estimate = {
            "hours": offline_hours,
            "days": round(offline_hours / 24, 1),
            "description": f"~{offline_hours} hours ({round(offline_hours / 24, 1)} days)",
        }

    return {
        "server_id": server_id,
        "ssh_health_status": server.ssh_health_status,
        "consecutive_failures": server.consecutive_ssh_failures,
        "failure_threshold": server.ssh_health_failure_threshold or 84,
        "is_ssh_down": server.is_ssh_down,
        "last_ssh_success": server.last_ssh_success.isoformat()
        if server.last_ssh_success
        else None,
        "last_ssh_failure": server.last_ssh_failure.isoformat()
        if server.last_ssh_failure
        else None,
        "last_health_check": server.last_ssh_health_check.isoformat()
        if server.last_ssh_health_check
        else None,
        "check_interval_hours": server.ssh_health_check_interval_hours or 2,
        "offline_duration_estimate": offline_duration_estimate,
        "monitoring_enabled": server.enable_ssh_health_monitoring,
    }
