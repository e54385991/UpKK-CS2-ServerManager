"""
Server management routes
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from typing import List, Dict, Any
import asyncio
import asyncssh
import shlex

from modules import (
    Server, ServerCreate, ServerUpdate, ServerResponse, ServerResponseWithUser, AuthType,
    get_db, User, UserResponse, get_current_active_user, get_current_admin_user, get_optional_current_user, generate_api_key,
    get_current_time, SystemSettings
)
from services import redis_manager
from services.captcha_service import captcha_service
from services.ssh_manager import SSHManager

router = APIRouter(prefix="/servers", tags=["servers"])


async def get_server_with_permission(
    server_id: int,
    current_user: User,
    db: AsyncSession
) -> Server:
    """
    Get server by ID, checking user permissions.
    Admins can access any server, regular users can only access their own.
    """
    if current_user.is_admin:
        server = await Server.get_by_id(db, server_id)
    else:
        server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server not found"
        )
    
    return server


@router.post("", response_model=ServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    server_data: ServerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a new CS2 server"""
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(server_data.captcha_token, server_data.captcha_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired CAPTCHA code"
        )
    
    # Check if server name already exists for this user
    existing = await Server.get_by_name_and_user(db, server_data.name, current_user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Server with name '{server_data.name}' already exists"
        )
    
    # Check if server with same host and game_directory already exists for this user
    duplicate_server = await Server.get_by_host_directory_and_user(
        db, server_data.host, server_data.game_directory, current_user.id
    )
    if duplicate_server:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A server with the same host ({server_data.host}) and game directory ({server_data.game_directory}) already exists. "
                   f"If you want to add a new server on this host, please use a different game directory or manually delete the existing directory on the server first."
        )
    
    # Validate SSH connection before creating server (password authentication only)
    conn = None
    try:
        if not server_data.ssh_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SSH password is required"
            )
        
        # Step 1: Attempt SSH connection
        try:
            conn = await asyncssh.connect(
                server_data.host,
                port=server_data.ssh_port,
                username=server_data.ssh_user,
                password=server_data.ssh_password,
                known_hosts=None,
                connect_timeout=15
            )
        except asyncssh.PermissionDenied:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH authentication failed for {server_data.ssh_user}@{server_data.host}. Please verify your username and password."
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"SSH connection to {server_data.host}:{server_data.ssh_port} timed out. The server may be unreachable or too slow to respond. Please check the network connection and server status."
            )
        except asyncssh.ConnectionLost as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Connection to {server_data.host}:{server_data.ssh_port} was lost. Please check if the server is reachable."
            )
        except asyncssh.Error as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH connection to {server_data.host}:{server_data.ssh_port} failed: {str(e)}. Please verify the host and port."
            )
        
        # Step 2: Test command execution
        result = await conn.run("echo 'SSH connection successful'", check=False)
        
        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH connection succeeded but command execution failed. Please verify that user {server_data.ssh_user} has proper shell access and permissions."
            )
        
        # Step 3: Create game directory with proper permissions
        # Use shlex.quote to safely escape the directory path
        game_dir_quoted = shlex.quote(server_data.game_directory)
        mkdir_cmd = f"mkdir -p {game_dir_quoted}"
        
        result = await conn.run(mkdir_cmd, check=False)
        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to create game directory {server_data.game_directory}. Please check permissions and path."
            )
        
        # Set proper permissions (755 - owner can read/write/execute, others can read/execute)
        chmod_cmd = f"chmod 755 {game_dir_quoted}"
        result = await conn.run(chmod_cmd, check=False)
        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to set permissions on game directory {server_data.game_directory}. Please check user permissions."
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to validate server connection: {str(e)}"
        )
    finally:
        # Ensure connection is always closed
        if conn:
            conn.close()
    
    # Create server with user_id, auto-generated API key, and password auth
    # Exclude captcha fields from server creation
    server_dict = server_data.model_dump(exclude={'captcha_token', 'captcha_code'})
    server_dict['auth_type'] = AuthType.PASSWORD  # Always use password authentication
    
    # Apply system default proxy settings if not explicitly set by user
    system_settings = await SystemSettings.get_settings(db)
    if system_settings:
        # If user hasn't explicitly set proxy mode, apply system defaults
        # Check if both proxy fields are in their default state (None/False)
        if not server_dict.get('use_panel_proxy') and not server_dict.get('github_proxy'):
            if system_settings.default_proxy_mode == 'panel':
                server_dict['use_panel_proxy'] = True
                server_dict['github_proxy'] = None
            elif system_settings.default_proxy_mode == 'github_url' and system_settings.github_proxy_url:
                server_dict['use_panel_proxy'] = False
                server_dict['github_proxy'] = system_settings.github_proxy_url
            # else: default_proxy_mode is 'direct', keep both as None/False
    
    server = Server(**server_dict, user_id=current_user.id, api_key=generate_api_key())
    db.add(server)
    await db.commit()
    await db.refresh(server)
    
    return server


@router.get("", response_model=List[ServerResponse])
async def list_servers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all servers owned by current user"""
    servers = await Server.get_all_by_user(db, current_user.id, skip, limit)
    return servers


@router.get("/admin/all", response_model=List[ServerResponseWithUser])
async def list_all_servers_admin(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """List all servers across all users (admin only)"""
    servers = await Server.get_all(db, skip, limit)
    
    # Early return if no servers
    if not servers:
        return []
    
    # Fetch all unique user IDs and load users in one query to avoid N+1
    user_ids = {server.user_id for server in servers}
    users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    users = {user.id: user for user in users_result.scalars().all()}
    
    # Build response with user information
    result = []
    for server in servers:
        server_dict = ServerResponse.model_validate(server).model_dump()
        user = users.get(server.user_id)
        server_dict['user'] = UserResponse.model_validate(user) if user else None
        result.append(ServerResponseWithUser(**server_dict))
    
    return result


@router.get("/disk-space-all")
async def get_all_servers_disk_space(
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
    disk_space_map = await system_info_helper.get_all_servers_disk_space(servers, force_refresh=force_refresh)
    
    # Convert to string keys for JSON
    response = {str(k): v for k, v in disk_space_map.items()}
    
    return {
        "servers": response,
        "timestamp": get_current_time().isoformat()
    }


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get server by ID - admins can access any server, users can only access their own"""
    server = await get_server_with_permission(server_id, current_user, db)
    return server


@router.put("/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: int,
    server_data: ServerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update server - admins can update any server, users can only update their own"""
    server = await get_server_with_permission(server_id, current_user, db)
    
    # Track if monitoring status changed
    old_monitoring_enabled = server.enable_panel_monitoring
    
    # Update fields using SQLModel's sqlmodel_update method
    update_data = server_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(server, key, value)
    
    await db.commit()
    await db.refresh(server)
    
    # Handle monitoring status change
    from services.server_monitor import server_monitor
    from services.ssh_manager import SSHManager
    
    new_monitoring_enabled = server.enable_panel_monitoring
    
    if new_monitoring_enabled and not old_monitoring_enabled:
        # Monitoring was enabled - start monitoring
        ssh_manager = SSHManager()
        server_monitor.start_monitoring(server_id, ssh_manager)
    elif not new_monitoring_enabled and old_monitoring_enabled:
        # Monitoring was disabled - stop monitoring
        server_monitor.stop_monitoring(server_id)
    
    # Clear cache
    await redis_manager.clear_server_cache(server_id)
    
    return server


@router.post("/{server_id}/apply-system-defaults", response_model=ServerResponse)
async def apply_system_defaults_to_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Apply system default proxy settings to a server"""
    server = await get_server_with_permission(server_id, current_user, db)
    
    # Get system settings
    system_settings = await SystemSettings.get_settings(db)
    if not system_settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="System settings not configured"
        )
    
    # Apply system default proxy mode
    if system_settings.default_proxy_mode == 'panel':
        server.use_panel_proxy = True
        server.github_proxy = None
    elif system_settings.default_proxy_mode == 'github_url':
        server.use_panel_proxy = False
        server.github_proxy = system_settings.github_proxy_url
    else:  # 'direct'
        server.use_panel_proxy = False
        server.github_proxy = None
    
    await db.commit()
    await db.refresh(server)
    
    # Clear cache
    await redis_manager.clear_server_cache(server_id)
    
    return server


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete server - admins can delete any server, users can only delete their own"""
    server = await get_server_with_permission(server_id, current_user, db)
    
    await db.delete(server)
    await db.commit()
    
    # Clear cache
    await redis_manager.clear_server_cache(server_id)
    
    return None


@router.get("/{server_id}/monitoring-logs")
async def get_monitoring_logs(
    server_id: int,
    limit: int = 50,
    event_type: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get monitoring logs for a server from Redis"""
    from services.redis_manager import redis_manager
    import logging
    logger = logging.getLogger(__name__)
    
    # Verify server exists and user has access
    server = await get_server_with_permission(server_id, current_user, db)
    
    # Get logs from Redis
    try:
        logs = await redis_manager.get_monitoring_logs(
            server_id=server_id,
            event_type=event_type,
            limit=limit
        )
        logger.info(f"Retrieved {len(logs)} monitoring logs from Redis for server {server_id}")
        return logs
    except Exception as e:
        logger.error(f"Failed to get monitoring logs from Redis: {e}")
        return []


@router.get("/ping", dependencies=[])
async def ping():
    """
    Ultra-simple ping endpoint with zero imports and explicit empty dependencies.
    The dependencies=[] explicitly overrides any global router dependencies.
    If this returns 'Not authenticated', the issue is external (proxy, wrong server, etc.)
    """
    return {"status": "ok", "message": "pong"}


@router.get("/a2s-cache-test", dependencies=[])
async def test_a2s_cache():
    """
    Simple test endpoint to verify routing works.
    The dependencies=[] explicitly overrides any global router dependencies.
    If this returns 200 but /a2s-cache returns 422, 
    then the issue is with the a2s-cache endpoint itself.
    """
    return {
        "status": "ok",
        "message": "Test endpoint working - no dependencies, no validation",
        "timestamp": get_current_time().isoformat(),
        "note": "If you see this with 200 OK, routing is working correctly"
    }


@router.get("/a2s-cache", dependencies=[])
async def get_all_servers_a2s_cache():
    """
    Get cached A2S information for all servers.
    
    Completely rewritten endpoint with zero dependencies to prevent any 422 errors.
    No database, no authentication, no validation - just pure data retrieval.
    
    IMPORTANT: This route MUST be defined before /{server_id}/a2s-info
    to avoid path parameter matching conflicts.
    """
    import logging
    
    logger = logging.getLogger(__name__)
    logger.info("=== A2S-CACHE ENDPOINT CALLED ===")
    logger.info(f"Function signature has {len(get_all_servers_a2s_cache.__code__.co_varnames)} parameters")
    
    # Initialize response with current timestamp
    response = {
        "servers": {},
        "timestamp": get_current_time().isoformat(),
        "debug": {
            "endpoint": "a2s-cache",
            "version": "2.0-no-deps",
            "parameters": 0
        }
    }
    
    try:
        # Import services only when needed to avoid circular dependencies
        from services.a2s_cache_service import a2s_cache_service
        from modules.database import async_session_maker
        from modules.models import Server
        from sqlmodel import select
        
        logger.info("Starting database query...")
        # Use a separate session to avoid dependency injection issues
        async with async_session_maker() as session:
            # Get all servers from database
            result = await session.execute(select(Server))
            servers = result.scalars().all()
            logger.info(f"Found {len(servers)} servers in database")
            
            # Get cached data for each server
            for server in servers:
                try:
                    cached_info = await a2s_cache_service.get_cached_info(server.id)
                    if cached_info:
                        response["servers"][str(server.id)] = cached_info
                        logger.debug(f"Retrieved cache for server {server.id}")
                except Exception as e:
                    logger.error(f"Error getting cache for server {server.id}: {e}")
                    # Add minimal error info
                    response["servers"][str(server.id)] = {
                        "success": False,
                        "error": "Cache unavailable"
                    }
        
        logger.info(f"Successfully returning data for {len(response['servers'])} servers")
    except Exception as e:
        logger.error(f"Error in a2s-cache endpoint: {e}", exc_info=True)
        # Always return success with error details, never raise
        response["error"] = str(e)
    
    logger.info("=== A2S-CACHE ENDPOINT COMPLETE ===")
    # Always return a valid dict response
    return response


@router.get("/{server_id}/a2s-info")
async def get_server_a2s_info(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get A2S query information for a server"""
    from services.a2s_query import a2s_service
    
    # Verify server exists and user has access
    server = await get_server_with_permission(server_id, current_user, db)
    
    # Use configured A2S host/port or fall back to server host/game_port
    query_host = server.a2s_query_host or server.host
    query_port = server.a2s_query_port or server.game_port
    
    # Query server info
    info_success, server_info = await a2s_service.query_server_info(query_host, query_port)
    
    # Query players if server info was successful
    players_success = False
    player_list = None
    if info_success:
        players_success, player_list = await a2s_service.query_players(query_host, query_port)
    
    response = {
        "query_host": query_host,
        "query_port": query_port,
        "success": info_success,
        "server_info": server_info,
        "players": player_list if players_success else [],
        "timestamp": get_current_time().isoformat()
    }
    
    return response


@router.get("/{server_id}/cpu-count")
async def get_server_cpu_count(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
                "message": f"Failed to connect: {msg}"
            }
        
        # Get CPU count using nproc command
        success, stdout, stderr = await ssh_manager.execute_command("nproc")
        
        if success and stdout.strip().isdigit():
            cpu_count = int(stdout.strip())
            return {
                "success": True,
                "cpu_count": cpu_count,
                "message": "CPU count retrieved successfully"
            }
        else:
            # Fallback to /proc/cpuinfo
            success, stdout, stderr = await ssh_manager.execute_command("grep -c ^processor /proc/cpuinfo")
            if success and stdout.strip().isdigit():
                cpu_count = int(stdout.strip())
                return {
                    "success": True,
                    "cpu_count": cpu_count,
                    "message": "CPU count retrieved successfully"
                }
            else:
                return {
                    "success": False,
                    "cpu_count": 32,  # Default fallback
                    "message": "Failed to detect CPU count, using default"
                }
    except Exception as e:
        return {
            "success": False,
            "cpu_count": 32,  # Default fallback
            "message": f"Error: {str(e)}"
        }
    finally:
        await ssh_manager.disconnect()


@router.get("/{server_id}/disk-space")
async def get_server_disk_space(
    server_id: int,
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        return {
            "success": True,
            "disk_space": disk_info,
            "server_directory": server.game_directory
        }
    else:
        return {
            "success": False,
            "message": "Failed to retrieve disk space information",
            "server_directory": server.game_directory
        }


@router.get("/{server_id}/check-deployment")
async def check_server_deployment(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server not found"
        )
    
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
                "error": True
            }
        
        verify_success, verify_stdout, _ = await ssh_manager.execute_command(verify_cmd)
        await ssh_manager.disconnect()
        
        is_deployed = verify_success and 'exists' in verify_stdout
        
        return {
            "is_deployed": is_deployed,
            "binary_path": binary_path,
            "message": "Server is deployed" if is_deployed else "Server is not deployed",
            "error": False
        }
    except Exception as e:
        return {
            "is_deployed": False,
            "binary_path": binary_path,
            "message": f"Error checking deployment: {str(e)}",
            "error": True
        }


@router.post("/{server_id}/ssh-reconnect")
async def manual_ssh_reconnect(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
            status_code=403,
            detail="You don't have permission to access this server"
        )
    
    # Use SSH health monitor to perform manual reconnection
    from services.ssh_health_monitor import ssh_health_monitor
    
    success, message = await ssh_health_monitor.manual_reconnect(server_id)
    
    if success:
        return {
            "success": True,
            "message": message,
            "ssh_health_status": "healthy"
        }
    else:
        return {
            "success": False,
            "message": message,
            "ssh_health_status": server.ssh_health_status
        }


@router.get("/{server_id}/ssh-health")
async def get_ssh_health_status(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get SSH health status for a server"""
    # Get server and verify ownership
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to access this server"
        )
    
    # Calculate offline duration estimate based on consecutive failures
    offline_duration_estimate = None
    if server.consecutive_ssh_failures > 0:
        check_interval_hours = server.ssh_health_check_interval_hours or 2
        offline_hours = server.consecutive_ssh_failures * check_interval_hours
        offline_duration_estimate = {
            "hours": offline_hours,
            "days": round(offline_hours / 24, 1),
            "description": f"~{offline_hours} hours ({round(offline_hours / 24, 1)} days)"
        }
    
    return {
        "server_id": server_id,
        "ssh_health_status": server.ssh_health_status,
        "consecutive_failures": server.consecutive_ssh_failures,
        "failure_threshold": server.ssh_health_failure_threshold or 84,
        "is_ssh_down": server.is_ssh_down,
        "last_ssh_success": server.last_ssh_success.isoformat() if server.last_ssh_success else None,
        "last_ssh_failure": server.last_ssh_failure.isoformat() if server.last_ssh_failure else None,
        "last_health_check": server.last_ssh_health_check.isoformat() if server.last_ssh_health_check else None,
        "check_interval_hours": server.ssh_health_check_interval_hours or 2,
        "offline_duration_estimate": offline_duration_estimate,
        "monitoring_enabled": server.enable_ssh_health_monitoring
    }
