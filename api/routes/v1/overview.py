"""Versioned overview aggregates for the dashboard."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from api.dependencies import ActiveUser, DatabaseSession
from modules import Server
from modules.models.servers import ServerStatus
from modules.utils import get_current_time
from services.a2s_cache_service import a2s_cache_service
from services.disk_space_service import disk_space_service

from .schemas import (
    A2SCacheListView,
    A2SCacheView,
    DiskSpaceListView,
    DiskSpaceView,
    OverviewSummary,
    SteamLatestVersionView,
)
from .ssh_pool import read_ssh_pool_view

router = APIRouter(prefix="/api/v1/overview", tags=["v1-overview"])

_ATTENTION_STATUSES = frozenset({ServerStatus.ERROR, ServerStatus.UNKNOWN})


def _a2s_view(server_id: int, cached: dict | None) -> A2SCacheView:
    if not cached or not isinstance(cached, dict):
        return A2SCacheView(server_id=server_id, cached=False)
    info = cached.get("server_info") if isinstance(cached.get("server_info"), dict) else {}
    success = bool(cached.get("success"))
    return A2SCacheView(
        server_id=server_id,
        cached=True,
        success=success,
        player_count=info.get("player_count") if success else None,
        max_players=info.get("max_players") if success else None,
        map_name=str(info.get("map_name") or "") or None if success else None,
        server_name=str(info.get("server_name") or "") or None if success else None,
        version=str(info.get("version") or "") or None if success else None,
        last_updated=_parse_steam_timestamp(cached.get("last_updated") or cached.get("timestamp")),
        response_time_ms=cached.get("response_time_ms") if success else None,
    )


def _disk_view(server_id: int, info: dict | None) -> DiskSpaceView:
    if not info:
        return DiskSpaceView(server_id=server_id, cached=False)
    return DiskSpaceView(
        server_id=server_id,
        cached=True,
        used_gb=info.get("used_gb"),
        total_gb=info.get("total_gb"),
        available_gb=info.get("available_gb"),
        used_percent=info.get("used_percent"),
    )


def _parse_steam_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@router.get("/summary", response_model=OverviewSummary)
async def read_overview_summary(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> OverviewSummary:
    """Aggregate operational counters across the current user's servers."""
    servers = await Server.get_all_by_user(db, current_user.id, skip=0, limit=1000)
    running = sum(1 for server in servers if server.status == ServerStatus.RUNNING)
    attention = sum(1 for server in servers if server.status in _ATTENTION_STATUSES)
    capacity = sum(server.max_players for server in servers)
    pool = await read_ssh_pool_view()
    return OverviewSummary(
        total=len(servers),
        running=running,
        attention=attention,
        capacity=capacity,
        ssh_connections=pool.connections,
        ssh_in_use=pool.in_use,
        ssh_idle=pool.idle,
        ssh_leases=pool.leases,
    )


@router.get("/steam-version", response_model=SteamLatestVersionView)
async def read_steam_latest_version(
    _current_user: ActiveUser,
) -> SteamLatestVersionView:
    """Return the Redis-cached Steam CS2 version. Does not call Steam or SSH."""
    cached = await a2s_cache_service.get_latest_steam_version()
    if not cached:
        return SteamLatestVersionView(available=False)
    version = str(cached.get("version") or "").strip()
    return SteamLatestVersionView(
        available=bool(version),
        version=version or None,
        message=str(cached.get("message") or "") or None,
        timestamp=_parse_steam_timestamp(cached.get("timestamp")),
    )


@router.get("/disk-space", response_model=DiskSpaceListView)
async def read_overview_disk_space(
    db: DatabaseSession,
    current_user: ActiveUser,
    scope: Literal["mine", "all"] = Query(default="mine"),
    force_refresh: bool = Query(default=False),
) -> DiskSpaceListView:
    """Cached disk map for the server list. Default reads never SSH."""
    if scope == "all":
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
        servers = await Server.get_all(db, skip=0, limit=1000)
    else:
        servers = await Server.get_all_by_user(db, current_user.id, skip=0, limit=1000)

    views: list[DiskSpaceView] = []
    for server in servers:
        _ok, info = await disk_space_service.get_disk_space(
            server,
            force_refresh=force_refresh,
            cache_only=not force_refresh,
        )
        views.append(_disk_view(int(server.id), info if isinstance(info, dict) else None))
    return DiskSpaceListView(servers=views, timestamp=get_current_time())


@router.get("/a2s-cache", response_model=A2SCacheListView)
async def read_overview_a2s_cache(
    db: DatabaseSession,
    current_user: ActiveUser,
    scope: Literal["mine", "all"] = Query(default="mine"),
    force_refresh: bool = Query(default=False),
) -> A2SCacheListView:
    """Cached A2S map for the server list. Default reads never query A2S or SSH."""
    if scope == "all":
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
        servers = await Server.get_all(db, skip=0, limit=1000)
    else:
        servers = await Server.get_all_by_user(db, current_user.id, skip=0, limit=1000)

    views: list[A2SCacheView] = []
    for server in servers:
        cached = (
            await a2s_cache_service.refresh_cached_info(server)
            if force_refresh
            else await a2s_cache_service.get_cached_info(int(server.id))
        )
        views.append(_a2s_view(int(server.id), cached if isinstance(cached, dict) else None))
    return A2SCacheListView(servers=views, timestamp=get_current_time())
