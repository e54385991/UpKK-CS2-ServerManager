"""Servers monitoring endpoints."""

# ruff: noqa: F403,F405

import logging
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import (
    get_admin_principal,
    get_unit_of_work,
    resolve_a2s_cache_service,
)
from cs2_manager.core import ErrorResponse, Principal
from cs2_manager.features.servers import (
    A2SCacheEnvelope,
    A2SQueryResponse,
    A2STestResponse,
    MonitoringLogResponse,
    PingResponse,
    ServerMonitoringRepository,
    ServerNotFoundError,
)
from cs2_manager.infrastructure import UnitOfWork
from modules.auth import get_current_principal

from .common import *

router = APIRouter(prefix="/servers", tags=["servers"])
logger = logging.getLogger(__name__)


def _uow_session(uow: UnitOfWork) -> AsyncSession:
    if uow.session is None:
        raise RuntimeError("Unit of work is not active")
    return uow.session


def _monitoring_redis(request: Request) -> Any:
    container = getattr(request.app.state, "container", None)
    redis = getattr(container, "redis", None)
    if redis is None or not callable(getattr(redis, "get_monitoring_logs", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring cache is unavailable",
        )
    return redis


def _server_not_found(exc: ServerNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get(
    "/{server_id}/monitoring-logs",
    response_model=list[MonitoringLogResponse],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def get_monitoring_logs(
    server_id: int,
    request: Request,
    limit: int = 50,
    event_type: str | None = None,
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_current_principal),
):
    """Get monitoring logs for a server from Redis"""
    repository = ServerMonitoringRepository(_uow_session(uow))
    try:
        await repository.require_a2s_target(server_id, current_user)
    except ServerNotFoundError as exc:
        raise _server_not_found(exc) from exc
    await uow.commit()
    monitoring_redis = _monitoring_redis(request)

    # Get logs from Redis
    try:
        logs = await monitoring_redis.get_monitoring_logs(
            server_id=server_id, event_type=event_type, limit=limit
        )
        logger.info(f"Retrieved {len(logs)} monitoring logs from Redis for server {server_id}")
        return logs
    except Exception as e:
        logger.error(f"Failed to get monitoring logs from Redis: {e}")
        return []


@router.get(
    "/ping",
    dependencies=[],
    response_model=PingResponse,
    status_code=status.HTTP_200_OK,
)
async def ping():
    """
    Ultra-simple ping endpoint with zero imports and explicit empty dependencies.
    The dependencies=[] explicitly overrides any global router dependencies.
    If this returns 'Not authenticated', the issue is external (proxy, wrong server, etc.)
    """
    return {"status": "ok", "message": "pong"}


@router.get(
    "/a2s-cache-test",
    description=("Admin-only test endpoint used to verify that A2S cache routing works."),
    response_model=A2STestResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
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
async def get_all_servers_a2s_cache(
    request: Request,
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_current_principal),
):
    """
    Get cached A2S information for servers visible to the authenticated user.

    IMPORTANT: This route MUST be defined before /{server_id}/a2s-info
    to avoid path parameter matching conflicts.
    """
    logger.info("A2S cache endpoint called for user %s", current_user.id)

    # Initialize response with current timestamp
    response = {
        "servers": {},
        "timestamp": get_current_time().isoformat(),
        "debug": {
            "endpoint": "a2s-cache",
            "version": "3.0-owner-filtered",
            "user_id": current_user.id,
            "authenticated": True,
        },
    }

    try:
        a2s_cache_service = resolve_a2s_cache_service(request)
        repository = ServerMonitoringRepository(_uow_session(uow))
        server_ids = await repository.visible_server_ids(current_user)
        await uow.commit()

        cached_by_server = await a2s_cache_service.get_cached_info_many(server_ids)
        response["servers"] = {
            str(server_id): cached
            for server_id, cached in cached_by_server.items()
            if cached is not None
        }

        logger.info(f"Successfully returning data for {len(response['servers'])} servers")
    except Exception as e:
        logger.error(f"Error in a2s-cache endpoint: {e}", exc_info=True)
        # Always return success with error details, never raise
        response["error"] = "Cache unavailable"

    logger.info("=== A2S-CACHE ENDPOINT COMPLETE ===")
    # Always return a valid dict response
    return response


@router.get(
    "/{server_id}/a2s-info",
    response_model=A2SQueryResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def get_server_a2s_info(
    server_id: int,
    uow: UnitOfWork = Depends(get_unit_of_work),
    current_user: Principal = Depends(get_current_principal),
):
    """Get A2S query information for a server"""
    from services.a2s_query import a2s_service

    repository = ServerMonitoringRepository(_uow_session(uow))
    try:
        target = await repository.require_a2s_target(server_id, current_user)
    except ServerNotFoundError as exc:
        raise _server_not_found(exc) from exc
    await uow.commit()

    # Query server info
    info_success, server_info = await a2s_service.query_server_info(
        target.query_host,
        target.query_port,
    )

    # Query players if server info was successful
    players_success = False
    player_list = None
    if info_success:
        players_success, player_list = await a2s_service.query_players(
            target.query_host,
            target.query_port,
        )

    response = {
        "query_host": target.query_host,
        "query_port": target.query_port,
        "success": info_success,
        "server_info": server_info,
        "players": player_list if players_success else [],
        "timestamp": get_current_time().isoformat(),
    }

    return response
