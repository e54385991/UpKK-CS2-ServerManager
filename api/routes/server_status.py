"""
Server status reporting routes
These endpoints are called by CS2 servers to report their status (crashes, restarts, etc.)
Authentication is done via API key rather than JWT
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel, select

from api.dependencies import get_admin_principal
from cs2_manager.core import ErrorResponse, Principal
from cs2_manager.infrastructure.credentials import hash_token
from modules import (
    DeploymentLog,
    Server,
    ServerStatus,
    get_db,
)
from modules.config import settings as default_settings
from modules.database import async_session_maker

router = APIRouter(prefix="/api/server-status", tags=["server-status"])

server_api_key_header = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ServerAgentApiKey",
    description="Server API key for authentication",
    auto_error=False,
)


class ServerStatusReport(SQLModel):
    """Schema for server status reports from CS2 servers"""

    event_type: str = Field(min_length=1, max_length=45)
    message: Optional[str] = Field(default=None, max_length=16_384)
    exit_code: Optional[int] = None
    restart_count: Optional[int] = Field(default=None, ge=0)
    crash_details: Optional[str] = Field(default=None, max_length=32_768)


class ServerStatusReportResponse(SQLModel):
    """Stable, secret-free acknowledgement returned to a server agent."""

    success: bool
    message: str
    server_id: int
    event_type: str
    current_status: ServerStatus


class ServerAgentConfigResponse(SQLModel):
    """Configuration fields which a server agent may read."""

    server_id: int
    name: str
    game_port: int
    default_map: str
    max_players: int
    game_mode: str
    game_type: str


class SSHConnectionPoolStats(SQLModel):
    """Non-sensitive aggregate connection-pool statistics."""

    total_connections: int
    alive_connections: int
    in_use_connections: int
    idle_connections: int
    idle_timeout: int
    max_lifetime: int
    max_connections: int
    available_capacity: int


class SSHConnectionPoolStatsResponse(SQLModel):
    success: bool
    pool_stats: SSHConnectionPoolStats


async def _pool_stats_legacy_db_compatibility() -> None:
    """Keep the legacy direct-call slot without opening a request DB session."""


@dataclass(frozen=True, slots=True)
class ServerAgentPrincipal:
    """Authenticated server-agent data detached from the database session."""

    id: int
    name: str
    status: ServerStatus
    game_port: int
    default_map: str
    max_players: int
    game_mode: str
    game_type: str


_SERVER_AGENT_COLUMNS = (
    Server.id.label("id"),
    Server.name.label("name"),
    Server.status.label("status"),
    Server.game_port.label("game_port"),
    Server.default_map.label("default_map"),
    Server.max_players.label("max_players"),
    Server.game_mode.label("game_mode"),
    Server.game_type.label("game_type"),
)


def _server_agent_from_row(row: Any) -> ServerAgentPrincipal:
    values = row._mapping
    return ServerAgentPrincipal(
        id=int(values["id"]),
        name=str(values["name"]),
        status=ServerStatus(values["status"]),
        game_port=int(values["game_port"]),
        default_map=str(values["default_map"]),
        max_players=int(values["max_players"]),
        game_mode=str(values["game_mode"]),
        game_type=str(values["game_type"]),
    )


def _token_hash_key(request: Request) -> str:
    app_settings = getattr(request.app.state, "settings", default_settings)
    return (
        getattr(app_settings, "TOKEN_HASH_KEY", "")
        or getattr(app_settings, "SECRET_KEY", "")
        or default_settings.SECRET_KEY
    )


async def _find_server_agent(
    db: AsyncSession,
    api_key: str,
    token_hash_key: str,
) -> ServerAgentPrincipal | None:
    """Resolve only non-secret agent data and verify a keyed digest.

    The plaintext-column query is limited to pre-backfill rows with no digest.
    It remains during the first credential-migration release, but the selected
    legacy value is still HMAC-verified before it is accepted.
    """

    supplied_digest = hash_token(api_key, token_hash_key)
    result = await db.execute(
        select(
            *_SERVER_AGENT_COLUMNS,
            Server.api_key_hash.label("_api_key_hash"),
        ).where(Server.api_key_hash == supplied_digest)
    )
    row = result.one_or_none()
    if row is not None:
        stored_digest = row._mapping["_api_key_hash"]
        if stored_digest is not None and hmac.compare_digest(
            str(stored_digest),
            supplied_digest,
        ):
            return _server_agent_from_row(row)
        return None

    # Compatibility path for rows which have not completed the first-release
    # hash backfill. Do not load the ORM entity: doing so would hydrate every
    # unrelated SSH, RCON, GSLT and webhook credential.
    legacy_api_key = Server.__table__.c.api_key
    result = await db.execute(
        select(
            *_SERVER_AGENT_COLUMNS,
            legacy_api_key.label("_legacy_api_key"),
        ).where(
            Server.api_key_hash.is_(None),
            legacy_api_key == api_key,
        )
    )
    row = result.one_or_none()
    if row is None:
        return None

    stored_legacy_key = row._mapping["_legacy_api_key"]
    if stored_legacy_key is None:
        return None
    stored_digest = hash_token(str(stored_legacy_key), token_hash_key)
    if not hmac.compare_digest(stored_digest, supplied_digest):
        return None
    return _server_agent_from_row(row)


async def verify_server_api_key(
    request: Request,
    x_api_key: str | None = Security(server_api_key_header),
) -> ServerAgentPrincipal:
    """
    Verify a server API key and return a detached, secret-free identity.

    Args:
        x_api_key: API key from request header

    Returns:
        Server agent identity if the keyed digest is valid

    Raises:
        HTTPException: If API key is invalid
    """
    if not x_api_key or len(x_api_key) > 256:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    container = getattr(request.app.state, "container", None)
    database = getattr(container, "database", None)
    session_factory = getattr(database, "session_factory", None) or async_session_maker

    async with session_factory() as db:
        server = await _find_server_agent(db, x_api_key, _token_hash_key(request))

    if server is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    return server


@router.post(
    "/{server_id}/report",
    response_model=ServerStatusReportResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    },
)
async def report_server_status(
    server_id: int,
    report: ServerStatusReport,
    server: ServerAgentPrincipal = Depends(verify_server_api_key),
    db: AsyncSession = Depends(get_db),
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
            detail="Server ID mismatch - cannot report for another server",
        )

    current_status = server.status
    log = DeploymentLog(
        server_id=server_id,
        action=f"auto_{report.event_type}",
        status="in_progress" if report.event_type in ["restart", "startup"] else "completed",
        output=report.message or f"Server reported {report.event_type} event",
    )

    if report.crash_details:
        log.error_message = report.crash_details

    db.add(log)

    # Update server status based on event type
    if report.event_type == "crash":
        current_status = ServerStatus.ERROR
        log.status = "failed"
    elif report.event_type == "restart":
        current_status = ServerStatus.RUNNING
        log.status = "success"
    elif report.event_type == "startup":
        current_status = ServerStatus.RUNNING
        log.status = "success"
    elif report.event_type == "shutdown":
        current_status = ServerStatus.STOPPED
        log.status = "success"
    elif report.event_type == "crash_limit_reached":
        current_status = ServerStatus.STOPPED
        log.status = "failed"
        log.error_message = (
            f"Server stopped due to excessive crashes. "
            f"Restart count: {report.restart_count}. "
            f"{report.message or 'Automatic restart disabled.'}"
        )

    if current_status != server.status:
        await db.execute(update(Server).where(Server.id == server_id).values(status=current_status))
    await db.commit()

    return {
        "success": True,
        "message": "Status report received",
        "server_id": server_id,
        "event_type": report.event_type,
        "current_status": current_status.value,
    }


@router.get(
    "/{server_id}/config",
    response_model=ServerAgentConfigResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    },
)
async def get_server_config(
    server_id: int,
    server: ServerAgentPrincipal = Depends(verify_server_api_key),
):
    """
    Get server configuration for the startup script.

    This endpoint can be called by the server to retrieve its configuration
    if needed by the startup script.

    Args:
        server_id: ID of the server requesting config
        server: Authenticated server instance (from API key)
    Returns:
        Server configuration data
    """
    # Verify that the server_id matches the authenticated server
    if server.id != server_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Server ID mismatch - cannot access another server's config",
        )

    return {
        "server_id": server.id,
        "name": server.name,
        "game_port": server.game_port,
        "default_map": server.default_map,
        "max_players": server.max_players,
        "game_mode": server.game_mode,
        "game_type": server.game_type,
    }


@router.get(
    "/pool/stats",
    response_model=SSHConnectionPoolStatsResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def get_ssh_pool_stats(
    request: Request,
    _: Principal = Depends(get_admin_principal),
    db: AsyncSession | None = Depends(_pool_stats_legacy_db_compatibility),
):
    """
    Get SSH connection pool statistics (admin endpoint for monitoring)

    Returns connection pool health and usage metrics.
    """
    # Older direct Python callers may still pass the legacy authentication
    # session positionally. ASGI requests resolve this slot to ``None``.
    if db is not None:
        await db.commit()

    pool = cast(Any, request.app.state.container.ssh_pool)
    if pool is None or not callable(getattr(pool, "get_pool_stats", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSH connection pool is unavailable",
        )
    stats = await pool.get_pool_stats()
    return {"success": True, "pool_stats": stats}
