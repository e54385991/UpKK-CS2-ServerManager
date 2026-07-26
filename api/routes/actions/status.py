"""Actions status endpoints."""

# ruff: noqa: F403,F405

from .common import *

router = APIRouter(tags=["actions"])


@router.get("/servers/{server_id}/ssh-connection-info")
async def get_ssh_connection_info(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get SSH connection information for a server.
    Returns connection status, age, reconnection count, and pooling status.
    """
    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user)

    # Get connection info from pool
    from services.ssh_connection_pool import ssh_connection_pool

    connection_info = await ssh_connection_pool.get_connection_info(server)

    return connection_info


@router.post("/servers/{server_id}/reconnect-ssh")
async def reconnect_ssh(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Manually reconnect SSH connection for a server.
    This bypasses rate limiting, resets the reconnection counter, and clears the SSH down flag.
    """
    from sqlalchemy import update as sql_update

    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user)

    # Clear the SSH down flag to allow reconnection
    if server.is_ssh_down:
        await db.execute(
            sql_update(Server)
            .where(Server.id == server_id)
            .values(is_ssh_down=False, consecutive_ssh_failures=0)
        )
        await db.commit()
        await db.refresh(server)

    # Perform manual reconnection through pool
    from services.ssh_connection_pool import ssh_connection_pool

    connection = None
    try:
        success, connection, msg = await ssh_connection_pool.manual_reconnect(server)
        if success:
            # Update ssh_health_status to healthy after successful reconnection
            now = get_current_time()
            await db.execute(
                sql_update(Server)
                .where(Server.id == server_id)
                .values(
                    ssh_health_status="healthy",
                    is_ssh_down=False,
                    consecutive_ssh_failures=0,
                    last_ssh_success=now,
                    last_ssh_health_check=now,
                )
            )
            await db.commit()

            return {"success": True, "message": msg}
        else:
            return {"success": False, "message": msg}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reconnect: {str(e)}",
        ) from e
    finally:
        if connection is not None:
            await ssh_connection_pool.release_connection(server, connection)


@router.post("/servers/{server_id}/reset-reconnect-counter")
async def reset_reconnect_counter(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Reset the reconnection counter for a server without reconnecting.
    """
    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user)

    # Reset counter through pool
    from services.ssh_connection_pool import ssh_connection_pool

    try:
        await ssh_connection_pool.reset_reconnection_counter(server)
        return {"success": True, "message": "重连计数已重置 | Reconnection counter reset"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset counter: {str(e)}",
        ) from e


@router.get("/servers/{server_id}/metamod-status")
async def get_metamod_status(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Check if Metamod:Source framework is installed on the server.
    Uses long-lived cache (1 hour) to avoid frequent SSH checks.

    Checks for the existence of:
    /cs2/game/csgo/addons/metamod/bin/linuxsteamrt64/metamod.2.cs2.so

    Returns:
        MetamodStatusResponse with installation status
    """
    from modules import MetamodStatusResponse

    # Get server and verify ownership
    server = await get_server_and_verify_ownership(db, server_id, current_user)

    # Create cache key
    cache_key = f"metamod_status:server:{server_id}"

    # Try to get from cache first (1 hour TTL)
    try:
        cached_status = await redis_manager.client.get(cache_key)
        if cached_status:
            # Parse cached JSON
            cached_data = json.loads(cached_status)
            return MetamodStatusResponse(**cached_data)
    except Exception as e:
        # Log but continue to check
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to get metamod status from cache: {e}")

    # Not in cache, check via SSH
    ssh_manager = SSHManager()
    success, msg = await ssh_manager.connect(server)

    if not success:
        return MetamodStatusResponse(
            success=False, installed=False, error=f"Failed to connect via SSH: {msg}"
        )

    try:
        # Check for metamod binary
        metamod_path = f"{server.game_directory}/cs2/game/csgo/addons/metamod/bin/linuxsteamrt64/metamod.2.cs2.so"
        check_cmd = f"test -f {metamod_path} && echo 'exists'"
        success, output, _ = await ssh_manager.execute_command(check_cmd)

        installed = "exists" in output

        result = MetamodStatusResponse(
            success=True,
            installed=installed,
            path=metamod_path if installed else None,
            message="Metamod:Source is installed"
            if installed
            else "Metamod:Source is not installed",
        )

        # Cache the result for 1 hour (3600 seconds)
        try:
            await redis_manager.client.setex(
                cache_key,
                3600,  # 1 hour TTL
                json.dumps(result.model_dump()),
            )
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to cache metamod status: {e}")

        return result

    except Exception as e:
        return MetamodStatusResponse(
            success=False, installed=False, error=f"Error checking metamod status: {str(e)}"
        )
    finally:
        await ssh_manager.disconnect()
