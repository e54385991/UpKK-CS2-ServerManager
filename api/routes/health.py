"""Service health endpoints."""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from api.metadata import APP_VERSION
from cs2_manager.core import (
    PROMETHEUS_CONTENT_TYPE,
    AppContainer,
    render_runtime_metrics,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    database: Literal["connected", "disconnected"]
    redis: Literal["connected", "disconnected"]
    version: str


class LivenessResponse(BaseModel):
    status: Literal["alive"]
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["connected", "disconnected"]
    redis: Literal["connected", "disconnected"]
    migrations: Literal["current", "outdated"]
    runtime: Literal["ready", "not_ready"]
    version: str


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> dict[str, str]:
    """Compatibility health check which always responds with HTTP 200."""
    checks = await _dependency_checks(request.app.state.container)
    return {
        "status": "healthy" if all(checks.values()) else "degraded",
        "database": _connection_label(checks["database"]),
        "redis": _connection_label(checks["redis"]),
        "version": APP_VERSION,
    }


@router.get("/livez", response_model=LivenessResponse)
async def liveness_check() -> dict[str, str]:
    """Report that the ASGI process can serve requests without external I/O."""
    return {"status": "alive", "version": APP_VERSION}


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness_check(request: Request, response: Response) -> dict[str, str]:
    """Require both database and Redis before accepting production traffic."""
    checks = await _dependency_checks(request.app.state.container)
    migrations_current = checks["database"] and await _migration_is_current(
        request.app.state.container
    )
    runtime_ready = _runtime_is_ready(request)
    ready = all(checks.values()) and migrations_current and runtime_ready
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "not_ready",
        "database": _connection_label(checks["database"]),
        "redis": _connection_label(checks["redis"]),
        "migrations": "current" if migrations_current else "outdated",
        "runtime": "ready" if runtime_ready else "not_ready",
        "version": APP_VERSION,
    }


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Expose process metrics only when an independent bearer token is set."""
    expected_token = getattr(request.app.state.settings, "METRICS_BEARER_TOKEN", None)
    if not expected_token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    authorization = request.headers.get("authorization", "")
    scheme, _, supplied_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied_token.encode("utf-8"),
        expected_token.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid metrics bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    content = await render_runtime_metrics(
        request.app.state.container,
        request.app.state.metrics,
    )
    return Response(
        content=content,
        media_type=PROMETHEUS_CONTENT_TYPE,
        headers={"Cache-Control": "no-store"},
    )


def _connection_label(connected: bool) -> str:
    return "connected" if connected else "disconnected"


async def _probe(name: str, operation: Awaitable[object]) -> bool:
    try:
        async with asyncio.timeout(2.0):
            return bool(await operation)
    except Exception as exc:
        logger.warning("%s readiness probe failed (%s)", name, type(exc).__name__)
        return False


async def _dependency_checks(container: AppContainer) -> dict[str, bool]:
    database, redis = await asyncio.gather(
        _probe("database", container.database.ping()),
        _probe("redis", container.redis.ping()),
    )
    return {"database": database, "redis": redis}


async def _migration_is_current(container: AppContainer) -> bool:
    """Check Alembic head without making health endpoints mutate the schema."""
    engine = getattr(container.database, "engine", None)
    if engine is None:
        # Lightweight test or extension adapters without an engine own their
        # readiness contract through ping(). Production adapters expose one.
        return True
    try:
        from cs2_manager.infrastructure.migrations import get_migration_status

        async with asyncio.timeout(2.0):
            migration_status = await get_migration_status(engine)
        return migration_status.is_current
    except Exception as exc:
        logger.warning("migration readiness probe failed (%s)", type(exc).__name__)
        return False


def _runtime_is_ready(request: Request) -> bool:
    """Reject traffic while owned runtime resources are stopping or stopped."""
    container = request.app.state.container
    if container.http is None or container.task_supervisor is None:
        return False
    if getattr(container.task_supervisor, "_closing", False):
        return False
    lifecycle = getattr(request.app.state, "lifecycle", None)
    return lifecycle is None or bool(getattr(lifecycle, "started", False))
