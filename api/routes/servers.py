"""
Server management routes
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
import asyncssh

from modules import (
    Server, ServerCreate, ServerUpdate, ServerResponse, AuthType,
    get_db, User, get_current_active_user, get_optional_current_user, generate_api_key,
    get_current_time
)
from services import redis_manager
from services.captcha_service import captcha_service

router = APIRouter(prefix="/servers", tags=["servers"])


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
    result = await db.execute(
        select(Server).filter(
            Server.name == server_data.name,
            Server.user_id == current_user.id
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Server with name '{server_data.name}' already exists"
        )
    
    # Check if server with same host and game_directory already exists for this user
    result = await db.execute(
        select(Server).filter(
            Server.host == server_data.host,
            Server.game_directory == server_data.game_directory,
            Server.user_id == current_user.id
        )
    )
    duplicate_server = result.scalar_one_or_none()
    if duplicate_server:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A server with the same host ({server_data.host}) and game directory ({server_data.game_directory}) already exists. "
                   f"If you want to add a new server on this host, please use a different game directory or manually delete the existing directory on the server first."
        )
    
    # Validate SSH connection before creating server (password authentication only)
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
                connect_timeout=10
            )
        except asyncssh.PermissionDenied:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH authentication failed for {server_data.ssh_user}@{server_data.host}. Please verify your username and password."
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
        conn.close()
        
        if result.exit_status != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSH connection succeeded but command execution failed. Please verify that user {server_data.ssh_user} has proper shell access and permissions."
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to validate server connection: {str(e)}"
        )
    
    # Create server with user_id, auto-generated API key, and password auth
    # Exclude captcha fields from server creation
    server_dict = server_data.model_dump(exclude={'captcha_token', 'captcha_code'})
    server_dict['auth_type'] = AuthType.PASSWORD  # Always use password authentication
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
    result = await db.execute(
        select(Server)
        .filter(Server.user_id == current_user.id)
        .offset(skip)
        .limit(limit)
    )
    servers = result.scalars().all()
    return servers


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get server by ID"""
    result = await db.execute(select(Server).filter(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server with ID {server_id} not found"
        )
    
    # Check ownership
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this server"
        )
    
    return server


@router.put("/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: int,
    server_data: ServerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update server"""
    result = await db.execute(select(Server).filter(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server with ID {server_id} not found"
        )
    
    # Check ownership
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this server"
        )
    
    # Track if monitoring status changed
    old_monitoring_enabled = server.enable_panel_monitoring
    
    # Update fields
    update_data = server_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(server, field, value)
    
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


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete server"""
    result = await db.execute(select(Server).filter(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server with ID {server_id} not found"
        )
    
    # Check ownership
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this server"
        )
    
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
    """Get monitoring logs for a server"""
    from modules.models import MonitoringLog
    from sqlalchemy import desc
    
    # Verify server exists and user has access
    result = await db.execute(select(Server).filter(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server with ID {server_id} not found"
        )
    
    # Check ownership
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this server's logs"
        )
    
    # Build query
    query = select(MonitoringLog).filter(MonitoringLog.server_id == server_id)
    
    # Filter by event type if specified
    if event_type:
        query = query.filter(MonitoringLog.event_type == event_type)
    
    # Order by most recent first and limit
    query = query.order_by(desc(MonitoringLog.created_at)).limit(limit)
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    # Convert to dict format for JSON response
    return [
        {
            "id": log.id,
            "server_id": log.server_id,
            "event_type": log.event_type,
            "status": log.status,
            "message": log.message,
            "created_at": log.created_at.isoformat() if log.created_at else None
        }
        for log in logs
    ]


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
        from sqlalchemy import select
        
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
    result = await db.execute(select(Server).filter(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server with ID {server_id} not found"
        )
    
    # Check ownership
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to query this server"
        )
    
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
