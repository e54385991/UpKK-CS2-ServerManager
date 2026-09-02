"""Servers monitoring endpoints."""

# ruff: noqa: F403,F405

from api.dependencies import ActiveUser, DatabaseSession

from .common import *

router = APIRouter(prefix="/servers", tags=["servers"])


@router.get("/{server_id}/monitoring-logs")
async def get_monitoring_logs(
    server_id: int,
    limit: int = 50,
    event_type: str | None = None,
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Get monitoring logs for a server from Redis"""
    import logging

    from services.redis_manager import redis_manager

    logger = logging.getLogger(__name__)

    # Verify server exists and user has access
    await get_server_with_permission(server_id, current_user, db)

    # Get logs from Redis
    try:
        logs = await redis_manager.get_monitoring_logs(
            server_id=server_id, event_type=event_type, limit=limit
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


@router.get(
    "/a2s-cache-test",
    dependencies=[],
    description=(
        "Simple test endpoint to verify routing works.\n"
        "The dependencies=[] explicitly overrides any global router dependencies.\n"
        "If this returns 200 but /a2s-cache returns 422, \n"
        "then the issue is with the a2s-cache endpoint itself."
    ),
)
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
        "note": "If you see this with 200 OK, routing is working correctly",
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
    logger.info(
        f"Function signature has {len(get_all_servers_a2s_cache.__code__.co_varnames)} parameters"
    )

    # Initialize response with current timestamp
    response = {
        "servers": {},
        "timestamp": get_current_time().isoformat(),
        "debug": {"endpoint": "a2s-cache", "version": "2.0-no-deps", "parameters": 0},
    }

    try:
        # Import services only when needed to avoid circular dependencies
        from sqlmodel import select

        from modules.database import async_session_maker
        from modules.models import Server
        from services.a2s_cache_service import a2s_cache_service

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
                        "error": "Cache unavailable",
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
    db: DatabaseSession,
    current_user: ActiveUser,
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
        "timestamp": get_current_time().isoformat(),
    }

    return response
