"""Top-level health and authenticated A2S cache routes."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import (
    get_admin_principal,
    get_unit_of_work,
    resolve_a2s_cache_service,
)
from cs2_manager.core import ErrorResponse, Principal
from cs2_manager.features.servers import (
    A2SCacheEnvelope,
    A2STestResponse,
    PublicPingResponse,
    ServerMonitoringRepository,
)
from cs2_manager.infrastructure import UnitOfWork
from modules.auth import get_current_principal
from modules.utils import get_current_time

# Create a router with NO prefix
router = APIRouter(tags=["cache"])


def _uow_session(uow: UnitOfWork) -> AsyncSession:
    if uow.session is None:
        raise RuntimeError("Unit of work is not active")
    return uow.session


@router.get(
    "/ping",
    response_model=PublicPingResponse,
    status_code=status.HTTP_200_OK,
)
async def ping():
    """
    Ultra-simple ping endpoint - completely public, no auth.
    If this returns 'Not authenticated', issue is external (reverse proxy, etc.)
    """
    return {"status": "ok", "message": "pong", "public": True}


@router.get(
    "/a2s-cache-test",
    response_model=A2STestResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def test_a2s_cache(_: Principal = Depends(get_admin_principal)):
    """Admin-only routing diagnostic for the A2S cache endpoint."""
    return {
        "status": "ok",
        "message": "A2S cache test endpoint working",
        "timestamp": get_current_time().isoformat(),
        "admin": True,
    }


@router.get(
    "/a2s-cache",
    response_model=A2SCacheEnvelope,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def get_user_servers_a2s_cache(
    request: Request,
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_current_principal),
):
    """
    Get cached A2S information for current user's servers.

    Requires authentication to filter servers by user UID.
    Returns only the servers belonging to the authenticated user.
    """
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"=== A2S-CACHE ENDPOINT CALLED for user {current_user.id} ===")

    # Initialize response
    response = {
        "servers": {},
        "timestamp": get_current_time().isoformat(),
        "debug": {
            "endpoint": "a2s-cache",
            "router": "cache",
            "version": "4.0-user-filtered",
            "user_id": current_user.id,
            "authenticated": True,
        },
    }

    try:
        a2s_cache_service = resolve_a2s_cache_service(request)
        repository = ServerMonitoringRepository(_uow_session(uow))
        server_ids = await repository.visible_server_ids(current_user)
        await uow.commit()

        logger.info("Found %s visible servers for user %s", len(server_ids), current_user.id)
        cached_by_server = await a2s_cache_service.get_cached_info_many(server_ids)
        response["servers"] = {
            str(server_id): cached
            for server_id, cached in cached_by_server.items()
            if cached is not None
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
        response["error"] = "Cache unavailable"

    logger.info("=== A2S-CACHE ENDPOINT COMPLETE ===")
    return response
