"""
Routes for a2s-cache - requires authentication to filter by user
Separate router to avoid /servers prefix issues
"""

from fastapi import APIRouter, Depends, Request

from modules.auth import get_current_active_user, get_current_admin_user
from modules.models import User
from modules.utils import get_current_time

# Create a router with NO prefix
router = APIRouter(tags=["cache"])


@router.get("/ping")
async def ping():
    """
    Ultra-simple ping endpoint - completely public, no auth.
    If this returns 'Not authenticated', issue is external (reverse proxy, etc.)
    """
    return {"status": "ok", "message": "pong", "public": True}


@router.get("/a2s-cache-test")
async def test_a2s_cache():
    """
    Test endpoint for a2s-cache - completely public, no auth.
    """
    return {
        "status": "ok",
        "message": "Public test endpoint working",
        "timestamp": get_current_time().isoformat(),
        "public": True,
    }


@router.get("/a2s-cache")
async def get_user_servers_a2s_cache(
    request: Request,
    current_user: User = Depends(get_current_active_user),
):
    """
    Get cached A2S information for current user's servers.

    Requires authentication to filter servers by user UID.
    Returns only the servers belonging to the authenticated user.
    """
    import logging

    from modules.database import async_session_maker

    # Import dependencies inside function
    from modules.models import Server
    from services.a2s_cache_service import a2s_cache_service

    admin_view = request.query_params.get("admin_view", "").lower() == "true"
    if admin_view:
        await get_current_admin_user(current_user)

    logger = logging.getLogger(__name__)
    cache_scope = "all servers" if admin_view else f"user {current_user.id}"
    logger.info(f"=== A2S-CACHE ENDPOINT CALLED for {cache_scope} ===")

    # Initialize response
    response = {
        "servers": {},
        "timestamp": get_current_time().isoformat(),
        "debug": {
            "endpoint": "a2s-cache",
            "router": "cache",
            "version": "5.0-admin-aware",
            "user_id": current_user.id,
            "authenticated": True,
            "admin_view": admin_view,
        },
    }

    try:
        # Use a separate session
        async with async_session_maker() as session:
            if admin_view:
                servers = await Server.get_all(session)
            else:
                servers = await Server.get_all_by_user(session, current_user.id)

            logger.info(f"Found {len(servers)} servers for {cache_scope}")

            # Get cached data for each server
            for server in servers:
                try:
                    cached_info = await a2s_cache_service.get_cached_info(server.id)
                    if cached_info:
                        response["servers"][str(server.id)] = cached_info
                        logger.debug(f"Retrieved cache for server {server.id}")
                except Exception as e:
                    logger.error(f"Error getting cache for server {server.id}: {e}")
                    response["servers"][str(server.id)] = {
                        "success": False,
                        "error": "Cache unavailable",
                    }

        # Add Steam latest version to response
        try:
            steam_version = await a2s_cache_service.get_latest_steam_version()
            if steam_version:
                response["steam_latest_version"] = steam_version
        except Exception as e:
            logger.error(f"Error getting Steam version: {e}")

        logger.info(f"Successfully returning data for {len(response['servers'])} servers")
    except Exception as e:
        logger.error(f"Error in a2s-cache endpoint: {e}", exc_info=True)
        response["error"] = str(e)

    logger.info("=== A2S-CACHE ENDPOINT COMPLETE ===")
    return response
