"""
Server status reporting routes
These endpoints are called by CS2 servers to report their status (crashes, restarts, etc.)
Authentication is done via API key rather than JWT
"""
from fastapi import APIRouter, Header, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from modules import Server, DeploymentLog, ServerStatus, get_db

router = APIRouter(prefix="/api/server-status", tags=["server-status"])


class ServerStatusReport(BaseModel):
    """Schema for server status reports from CS2 servers"""
    event_type: str  # "restart", "crash", "startup", "shutdown", "crash_limit_reached"
    message: Optional[str] = None
    exit_code: Optional[int] = None
    restart_count: Optional[int] = None
    crash_details: Optional[str] = None


async def verify_server_api_key(
    x_api_key: str = Header(..., description="Server API key for authentication"),
    db: AsyncSession = Depends(get_db)
) -> Server:
    """
    Verify server API key and return the server instance.
    
    Args:
        x_api_key: API key from request header
        db: Database session
    
    Returns:
        Server instance if API key is valid
    
    Raises:
        HTTPException: If API key is invalid
    """
    result = await db.execute(
        select(Server).filter(Server.api_key == x_api_key)
    )
    server = result.scalar_one_or_none()
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    
    return server


@router.post("/{server_id}/report")
async def report_server_status(
    server_id: int,
    report: ServerStatusReport,
    server: Server = Depends(verify_server_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Receive status reports from CS2 servers.
    
    This endpoint is called by the server startup script to report events like:
    - Server crashes
    - Automatic restarts
    - Crash limit reached (stopping auto-restart)
    - Normal startup/shutdown
    
    Args:
        server_id: ID of the reporting server
        report: Status report data
        server: Authenticated server instance (from API key)
        db: Database session
    
    Returns:
        Success response
    """
    # Verify that the server_id matches the authenticated server
    if server.id != server_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Server ID mismatch - cannot report for another server"
        )
    
    # Create deployment log entry
    log = DeploymentLog(
        server_id=server_id,
        action=f"auto_{report.event_type}",
        status="in_progress" if report.event_type in ["restart", "startup"] else "completed",
        output=report.message or f"Server reported {report.event_type} event"
    )
    
    if report.crash_details:
        log.error_message = report.crash_details
    
    db.add(log)
    
    # Update server status based on event type
    if report.event_type == "crash":
        server.status = ServerStatus.ERROR
        log.status = "failed"
    elif report.event_type == "restart":
        server.status = ServerStatus.RUNNING
        log.status = "success"
    elif report.event_type == "startup":
        server.status = ServerStatus.RUNNING
        log.status = "success"
    elif report.event_type == "shutdown":
        server.status = ServerStatus.STOPPED
        log.status = "success"
    elif report.event_type == "crash_limit_reached":
        server.status = ServerStatus.STOPPED
        log.status = "failed"
        log.error_message = (
            f"Server stopped due to excessive crashes. "
            f"Restart count: {report.restart_count}. "
            f"{report.message or 'Automatic restart disabled.'}"
        )
    
    await db.commit()
    
    return {
        "success": True,
        "message": "Status report received",
        "server_id": server_id,
        "event_type": report.event_type,
        "current_status": server.status.value
    }


@router.get("/{server_id}/config")
async def get_server_config(
    server_id: int,
    server: Server = Depends(verify_server_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Get server configuration for the startup script.
    
    This endpoint can be called by the server to retrieve its configuration
    if needed by the startup script.
    
    Args:
        server_id: ID of the server requesting config
        server: Authenticated server instance (from API key)
        db: Database session
    
    Returns:
        Server configuration data
    """
    # Verify that the server_id matches the authenticated server
    if server.id != server_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Server ID mismatch - cannot access another server's config"
        )
    
    return {
        "server_id": server.id,
        "name": server.name,
        "game_port": server.game_port,
        "default_map": server.default_map,
        "max_players": server.max_players,
        "tickrate": server.tickrate,
        "game_mode": server.game_mode,
        "game_type": server.game_type
    }


@router.get("/pool/stats")
async def get_ssh_pool_stats():
    """
    Get SSH connection pool statistics (admin endpoint for monitoring)
    
    Returns connection pool health and usage metrics.
    """
    from services.ssh_connection_pool import ssh_connection_pool
    
    stats = await ssh_connection_pool.get_pool_stats()
    return {
        "success": True,
        "pool_stats": stats
    }

