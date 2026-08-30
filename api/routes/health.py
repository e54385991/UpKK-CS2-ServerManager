"""Service health endpoints."""

import platform

import fastapi
from fastapi import APIRouter

from api.metadata import APP_VERSION
from services import redis_manager

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    redis_status = await redis_manager.ping()
    return {
        "status": "healthy",
        "redis": "connected" if redis_status else "disconnected",
        "version": APP_VERSION,
        "python": platform.python_version(),
        "fastapi": getattr(fastapi, "__version__", "") or "unknown",
    }
