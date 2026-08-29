"""Versioned server endpoints returning non-secret projections."""

from fastapi import APIRouter

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from modules import Server

from .schemas import ServerDetail, ServerSummary

router = APIRouter(prefix="/api/v1/servers", tags=["v1-servers"])


def _to_summary(server: Server) -> ServerSummary:
    return ServerSummary(
        id=server.id,
        name=server.name,
        host=server.host,
        game_port=server.game_port,
        status=server.status,
        description=server.description,
        default_map=server.default_map,
        max_players=server.max_players,
    )


def _to_detail(server: Server) -> ServerDetail:
    return ServerDetail(
        id=server.id,
        name=server.name,
        host=server.host,
        game_port=server.game_port,
        status=server.status,
        description=server.description,
        default_map=server.default_map,
        max_players=server.max_players,
        ssh_port=server.ssh_port,
        ssh_user=server.ssh_user,
        game_directory=server.game_directory,
        game_mode=server.game_mode,
        game_type=server.game_type,
        created_at=server.created_at,
        updated_at=server.updated_at,
        last_deployed=server.last_deployed,
    )


@router.get("", response_model=list[ServerSummary])
async def list_servers(
    db: DatabaseSession,
    current_user: ActiveUser,
    skip: int = 0,
    limit: int = 100,
) -> list[ServerSummary]:
    """List the current user's servers as non-secret summaries."""
    servers = await Server.get_all_by_user(db, current_user.id, skip, limit)
    return [_to_summary(server) for server in servers]


@router.get("/{server_id}", response_model=ServerDetail)
async def get_server(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerDetail:
    """Return one server the caller may access (owner or admin), non-secret."""
    server = await require_server_access(db, server_id, current_user)
    return _to_detail(server)
