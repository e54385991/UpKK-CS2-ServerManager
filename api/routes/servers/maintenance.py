"""Servers maintenance endpoints."""

# ruff: noqa: F403,F405

from .common import *

router = APIRouter(prefix="/servers", tags=["servers"])


@router.get("/disk-space-all")
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
    servers = await Server.get_all_by_user(db, current_user.id)

    # Get disk space for all servers
    disk_space_map = await system_info_helper.get_all_servers_disk_space(
        servers, force_refresh=force_refresh
    )

    # Convert to string keys for JSON
    response = {str(k): v for k, v in disk_space_map.items()}

    return {"servers": response, "timestamp": get_current_time().isoformat()}


@router.get("/{server_id}/cleanup/scan", response_model=CleanupScanResponse)
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
        try:
            await ssh_manager.disconnect()
        except Exception:
            pass


@router.post("/{server_id}/cleanup/delete", response_model=CleanupDeleteResponse)
async def delete_server_cleanup_items(
    server_id: int,
    cleanup_data: CleanupDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Delete approved game directory cleanup candidates for this server"""
    server = await get_server_with_permission(server_id, current_user, db)
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
        try:
            await ssh_manager.disconnect()
        except Exception:
            pass


@router.get("/{server_id}/s3-backups", response_model=List[S3BackupItem])
async def list_server_s3_backups(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List S3 plugin backups for this server"""
    server = await get_server_with_permission(server_id, current_user, db)
    owner = await get_server_owner_user(db, server, current_user)

    success, backups, error = await s3_backup_service.list_backups(owner, server)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    return backups


@router.post("/{server_id}/s3-restore")
async def restore_server_s3_backup(
    server_id: int,
    restore_data: S3RestoreRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Restore a selected S3 plugin backup to this server"""
    server = await get_server_with_permission(server_id, current_user, db)
    owner = await get_server_owner_user(db, server, current_user)

    if not s3_backup_service.validate_object_key(owner, server, restore_data.object_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Selected S3 backup does not belong to this server",
        )

    temp_dir = tempfile.mkdtemp(prefix="cs2_s3_restore_")
    local_path = os.path.join(
        temp_dir, s3_backup_service.safe_object_filename(restore_data.object_key)
    )
    ssh_manager = SSHManager()

    try:
        download_success, download_error = await s3_backup_service.download_backup(
            owner,
            server,
            restore_data.object_key,
            local_path,
        )
        if not download_success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=download_error)

        safety_success, safety_message = await ssh_manager.backup_plugins(server)
        if not safety_success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to create safety backup before restore: {safety_message}",
            )

        game_dir = server.game_directory.rstrip("/")
        filename = s3_backup_service.safe_object_filename(restore_data.object_key)
        remote_restore_path = f"{game_dir}/backups/s3-restore-{uuid.uuid4().hex[:8]}-{filename}"

        upload_success, upload_error = await ssh_manager.upload_file(
            local_path, remote_restore_path, server
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
            server,
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
        try:
            await ssh_manager.disconnect()
        except Exception:
            pass
        await to_thread.run_sync(lambda: shutil.rmtree(temp_dir, ignore_errors=True))


@router.get("/{server_id}/cpu-count")
async def get_server_cpu_count(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get CPU core count from the remote server"""
    from services.ssh_manager import SSHManager

    # Verify server exists and user has access
    server = await get_server_with_permission(server_id, current_user, db)

    ssh_manager = SSHManager()

    try:
        # Connect to server
        success, msg = await ssh_manager.connect(server)
        if not success:
            return {
                "success": False,
                "cpu_count": 32,  # Default fallback
                "message": f"Failed to connect: {msg}",
            }

        # Get CPU count using nproc command
        success, stdout, stderr = await ssh_manager.execute_command("nproc")

        if success and stdout.strip().isdigit():
            cpu_count = int(stdout.strip())
            return {
                "success": True,
                "cpu_count": cpu_count,
                "message": "CPU count retrieved successfully",
            }
        else:
            # Fallback to /proc/cpuinfo
            success, stdout, stderr = await ssh_manager.execute_command(
                "grep -c ^processor /proc/cpuinfo"
            )
            if success and stdout.strip().isdigit():
                cpu_count = int(stdout.strip())
                return {
                    "success": True,
                    "cpu_count": cpu_count,
                    "message": "CPU count retrieved successfully",
                }
            else:
                return {
                    "success": False,
                    "cpu_count": 32,  # Default fallback
                    "message": "Failed to detect CPU count, using default",
                }
    except Exception as e:
        return {
            "success": False,
            "cpu_count": 32,  # Default fallback
            "message": f"Error: {str(e)}",
        }
    finally:
        await ssh_manager.disconnect()


@router.get("/{server_id}/disk-space")
async def get_server_disk_space(
    server_id: int,
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get disk space information for server directory

    Args:
        force_refresh: If True, bypass cache and read from system
    """
    from services.system_info_helper import system_info_helper

    # Verify server exists and user has access
    server = await get_server_with_permission(server_id, current_user, db)

    # Get disk space info from system info helper
    disk_info = await system_info_helper.get_disk_space(server, force_refresh=force_refresh)

    if disk_info:
        return {"success": True, "disk_space": disk_info, "server_directory": server.game_directory}
    else:
        return {
            "success": False,
            "message": "Failed to retrieve disk space information",
            "server_directory": server.game_directory,
        }


@router.get("/{server_id}/check-deployment")
async def check_server_deployment(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    # Check if cs2 binary exists
    ssh_manager = SSHManager()

    binary_path = f"{server.game_directory}/cs2/game/bin/linuxsteamrt64/cs2"
    verify_cmd = f"test -f {binary_path} && echo 'exists' || echo 'missing'"

    try:
        success, msg = await ssh_manager.connect(server)
        if not success:
            return {
                "is_deployed": False,
                "binary_path": binary_path,
                "message": f"Could not connect to server: {msg}",
                "error": True,
            }

        verify_success, verify_stdout, _ = await ssh_manager.execute_command(verify_cmd)
        await ssh_manager.disconnect()

        is_deployed = verify_success and "exists" in verify_stdout

        return {
            "is_deployed": is_deployed,
            "binary_path": binary_path,
            "message": "Server is deployed" if is_deployed else "Server is not deployed",
            "error": False,
        }
    except Exception as e:
        return {
            "is_deployed": False,
            "binary_path": binary_path,
            "message": f"Error checking deployment: {str(e)}",
            "error": True,
        }


@router.post(
    "/{server_id}/ssh-reconnect",
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


@router.get("/{server_id}/ssh-health")
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
