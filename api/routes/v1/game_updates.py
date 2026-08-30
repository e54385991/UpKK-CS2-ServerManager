"""Versioned CS2 game version + auto-update workspace for one server."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request, status

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from api.routes.servers.crud import update_server as update_legacy_server
from modules import ServerUpdate
from modules.utils import get_current_time
from services.game_version import GameVersionStatus, inspect_game_version

from .operations import start_server_operation
from .schemas import (
    GameUpdateOperationRequest,
    GameUpdatesSettingsRequest,
    GameUpdatesView,
    ServerOperationRequest,
    ServerOperationView,
)

router = APIRouter(prefix="/api/v1/servers", tags=["v1-game-updates"])
logger = logging.getLogger(__name__)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _view(server, snapshot: GameVersionStatus) -> GameUpdatesView:
    return GameUpdatesView(
        installed_version=_optional_text(snapshot.installed_version),
        installed_build_id=_optional_text(snapshot.installed_build_id),
        installed_source=snapshot.installed_source,
        advertised_version=_optional_text(snapshot.advertised_version),
        up_to_date=snapshot.up_to_date,
        steam_check_ok=snapshot.steam_check_ok,
        steam_message=snapshot.steam_message,
        steam_error=snapshot.steam_error,
        enable_auto_update=bool(getattr(server, "enable_auto_update", True)),
        update_check_interval_hours=float(
            getattr(server, "update_check_interval_hours", 1.0) or 1.0
        ),
        last_update_check=getattr(server, "last_update_check", None),
        last_update_time=getattr(server, "last_update_time", None),
        current_game_version=_optional_text(getattr(server, "current_game_version", None)),
    )


async def _inspect(server, *, refresh: bool) -> GameVersionStatus:
    return await inspect_game_version(server, refresh=refresh)


def _degraded_snapshot(server) -> GameVersionStatus:
    installed = getattr(server, "current_game_version", None)
    return GameVersionStatus(
        installed_version=installed,
        installed_build_id=None,
        installed_source="database" if installed else "unknown",
        advertised_version=None,
        up_to_date=None,
        steam_check_ok=False,
        steam_message=None,
        steam_error=(
            "Cannot reach Steam from this panel. In Docker, allow outbound HTTPS "
            "to api.steampowered.com or set HTTPS_PROXY on the API container."
        ),
    )


async def _safe_inspect(server, *, refresh: bool) -> GameVersionStatus:
    try:
        return await _inspect(server, refresh=refresh)
    except Exception:
        logger.exception("game-updates inspect failed for server %s", getattr(server, "id", "?"))
        return _degraded_snapshot(server)


@router.get("/{server_id}/game-updates", response_model=GameUpdatesView)
async def get_game_updates(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    refresh: bool = Query(default=False),
) -> GameUpdatesView:
    server = await require_server_access(db, server_id, current_user)
    snapshot = await _safe_inspect(server, refresh=refresh)
    if refresh:
        server.last_update_check = get_current_time()
        if snapshot.installed_version:
            server.current_game_version = snapshot.installed_version
        await db.commit()
        await db.refresh(server)
    return _view(server, snapshot)


@router.put("/{server_id}/game-updates", response_model=GameUpdatesView)
async def update_game_updates(
    server_id: int,
    body: GameUpdatesSettingsRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> GameUpdatesView:
    updated = await update_legacy_server(
        server_id,
        ServerUpdate(
            enable_auto_update=body.enable_auto_update,
            update_check_interval_hours=body.update_check_interval_hours,
        ),
        db,
        current_user,
        request,
    )
    snapshot = await _safe_inspect(updated, refresh=False)
    return _view(updated, snapshot)


@router.post(
    "/{server_id}/game-updates/operations",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_game_update_operation(
    server_id: int,
    body: GameUpdateOperationRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Start SteamCMD update/validate through the shared operation hub (202 + SSE)."""
    await require_server_access(db, server_id, current_user, commit=False)
    return await start_server_operation(
        server_id,
        ServerOperationRequest(action=body.action),
        request,
        db,
        current_user,
    )
