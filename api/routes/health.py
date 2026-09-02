"""Service health endpoints."""

import platform

import fastapi
from fastapi import APIRouter

from api.contracts.health import HealthResponse
from api.metadata import APP_VERSION, BUILD_COMMIT, BUILD_TIME
from services import redis_manager

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint"""
    redis_status = await redis_manager.ping()
    return HealthResponse(
        status="healthy",
        redis="connected" if redis_status else "disconnected",
        version=APP_VERSION,
        python=platform.python_version(),
        fastapi=getattr(fastapi, "__version__", "") or "unknown",
        git_sha=BUILD_COMMIT,
        build_time=BUILD_TIME,
    )
