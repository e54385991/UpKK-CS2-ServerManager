"""
Routes for a2s-cache - requires authentication to filter by user
Separate router to avoid /servers prefix issues
"""
from fastapi import APIRouter, Depends
from datetime import datetime, timezone
from modules.models import User
from modules.auth import get_current_active_user

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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "public": True
    }


@router.get("/a2s-cache")
async def get_user_servers_a2s_cache(current_user: User = Depends(get_current_active_user)):
    """
    Get cached A2S information for current user's servers.
    
    Requires authentication to filter servers by user UID.
    Returns only the servers belonging to the authenticated user.
    """
    import logging
    
    # Import dependencies inside function
    from modules.models import Server
    from modules.database import async_session_maker
    from services.a2s_cache_service import a2s_cache_service
    from sqlalchemy import select
    
    logger = logging.getLogger(__name__)
    logger.info(f"=== A2S-CACHE ENDPOINT CALLED for user {current_user.id} ===")
    
    # Initialize response
    response = {
        "servers": {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "debug": {
            "endpoint": "a2s-cache",
            "router": "cache",
            "version": "4.0-user-filtered",
            "user_id": current_user.id,
            "authenticated": True
        }
    }
    
    try:
        # Use a separate session
        async with async_session_maker() as session:
            # Get servers for current user only
            result = await session.execute(
                select(Server).filter(Server.user_id == current_user.id)
            )
            
            servers = result.scalars().all()
            logger.info(f"Found {len(servers)} servers for user {current_user.id}")
            
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
                        "error": "Cache unavailable"
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

