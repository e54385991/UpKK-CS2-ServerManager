"""Versioned server endpoints returning non-secret projections."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from api.presenters.servers import to_detail as _to_detail
from api.presenters.servers import to_summary as _to_summary
from api.routes.actions.status import reconnect_ssh as reconnect_ssh_legacy
from api.routes.servers.configuration import get_startup_command as get_startup_command_legacy
from api.routes.servers.crud import apply_system_defaults_to_server as apply_defaults_legacy
from api.routes.servers.crud import create_server as create_legacy_server
from api.routes.servers.crud import delete_server as delete_legacy_server
from api.routes.servers.crud import update_server as update_legacy_server
from api.routes.servers.maintenance import (
    confirm_server_deployment as confirm_deployment_legacy,
)
from modules import Server, ServerCreate, ServerUpdate, User
from services.a2s_cache_service import a2s_cache_service
from services.disk_space_service import disk_space_service
from services.host_initialization import host_initialization_of
from services.maintenance_lock import maintenance_lock_service
from services.redis_manager import redis_manager
from services.server_operation_hub import ServerOperationConflict

from .operation_runner import enqueue_apply_apt_mirror
from .operations import to_view
from .overview import _a2s_view
from .schemas import (
    A2SCacheView,
    A2SPlayerView,
    A2SQueryView,
    A2SServerInfoView,
    ActionResult,
    AptMirrorApplyRequest,
    ConfirmDeploymentView,
    DiskSpaceView,
    MonitoringLogListView,
    MonitoringLogView,
    ServerCreateRequest,
    ServerCreateResult,
    ServerDetail,
    ServerOperationView,
    ServerSummary,
    ServerUpdateRequest,
    ServerWriteResult,
    StartupCommandView,
)

router = APIRouter(prefix="/api/v1/servers", tags=["v1-servers"])


async def _owners_by_id(db, servers: list[Server]) -> dict[int, User]:
    user_ids = {server.user_id for server in servers if getattr(server, "user_id", None)}
    if not user_ids:
        return {}
    result = await db.execute(select(User).where(User.id.in_(user_ids)))
    return {user.id: user for user in result.scalars().all()}


@router.post("", response_model=ServerCreateResult, status_code=status.HTTP_201_CREATED)
async def create_server(
    body: ServerCreateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> ServerCreateResult:
    """Create a server after CAPTCHA + SSH checks, then initialize host packages."""
    server = await create_legacy_server(
        ServerCreate(**body.model_dump()),
        db,
        current_user,
        request,
    )
    detail = await _to_detail(server)
    init = host_initialization_of(server)
    if init is None:
        return ServerCreateResult(
            **detail.model_dump(),
            host_initialized=True,
            initialization_message="",
        )
    return ServerCreateResult(
        **detail.model_dump(),
        host_initialized=init.success,
        missing_packages=list(init.missing_after or init.missing_before),
        manual_install_command=init.manual_install_command,
        initialization_message=init.message,
    )


@router.get("", response_model=list[ServerSummary])
async def list_servers(
    db: DatabaseSession,
    current_user: ActiveUser,
    skip: int = 0,
    limit: int = 100,
    scope: Literal["mine", "all"] = Query(default="mine"),
) -> list[ServerSummary]:
    """List servers the caller may see. ``scope=all`` is admin-only fleet view."""
    if scope == "all":
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
        servers = await Server.get_all(db, skip, limit)
        owners = await _owners_by_id(db, servers)
        return [_to_summary(server, owners.get(server.user_id)) for server in servers]

    servers = await Server.get_all_by_user(db, current_user.id, skip, limit)
    return [_to_summary(server) for server in servers]


@router.get("/{server_id}", response_model=ServerDetail)
async def get_server(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerDetail:
    """Return one server the caller may access (owner or admin), non-secret."""
    server = await require_server_access(db, server_id, current_user)
    return await _to_detail(server)


@router.patch("/{server_id}", response_model=ServerWriteResult)
async def update_server(
    server_id: int,
    body: ServerUpdateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> ServerWriteResult:
    """Patch non-secret settings. Omitted secrets stay unchanged."""
    updated = await update_legacy_server(
        server_id,
        ServerUpdate(**body.model_dump(exclude_unset=True)),
        db,
        current_user,
        request,
    )
    detail = await _to_detail(updated)
    return ServerWriteResult(
        **detail.model_dump(),
        restart_required=bool(getattr(updated, "restart_required", False)),
    )


@router.delete("/{server_id}", response_model=ActionResult)
async def delete_server(
    server_id: int,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    """Remove the panel record. Game files on the host are not uninstalled."""
    await delete_legacy_server(server_id, db, current_user, request)
    return ActionResult(success=True, message="Server deleted")


@router.post("/{server_id}/apply-system-defaults", response_model=ServerWriteResult)
async def apply_server_system_defaults(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerWriteResult:
    """Copy the panel default download-proxy mode onto this server."""
    updated = await apply_defaults_legacy(server_id, db, current_user)
    detail = await _to_detail(updated)
    return ServerWriteResult(
        **detail.model_dump(),
        restart_required=bool(getattr(updated, "restart_required", False)),
    )


@router.post(
    "/{server_id}/apt-mirror",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def apply_server_apt_mirror(
    server_id: int,
    body: AptMirrorApplyRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Persist the chosen apt mirror, rewrite host sources, and retry packages."""
    server = await require_server_access(db, server_id, current_user)
    server.apt_mirror = body.mirror
    await db.commit()
    await db.refresh(server)

    if await redis_manager.get(f"deployment_lock:{server_id}"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Server is currently being deployed or has a stuck deployment lock. "
                "Clear the lock before switching apt mirrors."
            ),
        )
    if await maintenance_lock_service.is_locked(server_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another operation already holds the server lock.",
        )

    try:
        record = await enqueue_apply_apt_mirror(
            server_id=server_id,
            mirror=body.mirror,
            actor_user_id=current_user.id,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return to_view(record)


@router.post("/{server_id}/ssh-reconnect", response_model=ActionResult)
async def reconnect_server_ssh(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ActionResult:
    """Clear the SSH-down flag and force a pool reconnect, same as the legacy UI."""
    result = await reconnect_ssh_legacy(server_id, db, current_user)
    return ActionResult(success=bool(result["success"]), message=str(result["message"]))


@router.get("/{server_id}/disk-space", response_model=DiskSpaceView)
async def get_server_disk_space(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    force_refresh: bool = Query(default=False),
) -> DiskSpaceView:
    """Cached game-directory disk snapshot. Default reads never SSH."""
    server = await require_server_access(db, server_id, current_user)
    _ok, info = await disk_space_service.get_disk_space(
        server,
        force_refresh=force_refresh,
        cache_only=not force_refresh,
    )
    if not info:
        return DiskSpaceView(server_id=int(server.id), cached=False)
    return DiskSpaceView(
        server_id=int(server.id),
        cached=True,
        used_gb=info.get("used_gb"),
        total_gb=info.get("total_gb"),
        available_gb=info.get("available_gb"),
        used_percent=info.get("used_percent"),
    )


@router.get("/{server_id}/a2s-cache", response_model=A2SCacheView)
async def get_server_a2s_cache(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    force_refresh: bool = Query(default=False),
) -> A2SCacheView:
    """Cached A2S snapshot for one server. Default reads never query A2S or SSH."""
    server = await require_server_access(db, server_id, current_user)
    cached = (
        await a2s_cache_service.refresh_cached_info(server)
        if force_refresh
        else await a2s_cache_service.get_cached_info(int(server.id))
    )
    return _a2s_view(int(server.id), cached if isinstance(cached, dict) else None)


def _parse_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _a2s_query_view(
    server: Server,
    cached: dict[str, Any] | None,
    *,
    live: bool,
) -> A2SQueryView:
    query_host = str(getattr(server, "a2s_query_host", None) or server.host)
    query_port = int(getattr(server, "a2s_query_port", None) or server.game_port)
    if not cached:
        return A2SQueryView(
            query_host=query_host,
            query_port=query_port,
            success=False,
            cached=False,
            live=live,
        )
    info_raw = cached.get("server_info")
    info = A2SServerInfoView.model_validate(info_raw) if isinstance(info_raw, dict) else None
    players: list[A2SPlayerView] = []
    raw_players = cached.get("players")
    if isinstance(raw_players, list):
        for item in raw_players:
            if isinstance(item, dict):
                players.append(A2SPlayerView.model_validate(item))
    return A2SQueryView(
        query_host=str(cached.get("query_host") or query_host),
        query_port=int(cached.get("query_port") or query_port),
        success=bool(cached.get("success")),
        cached=True,
        live=live,
        server_info=info if cached.get("success") else None,
        players=players,
        timestamp=_parse_optional_datetime(cached.get("timestamp")),
        last_updated=_parse_optional_datetime(
            cached.get("last_updated") or cached.get("timestamp")
        ),
        response_time_ms=int(cached["response_time_ms"])
        if cached.get("response_time_ms") is not None
        else None,
        error=str(cached["error"]) if cached.get("error") else None,
    )


@router.get("/{server_id}/a2s", response_model=A2SQueryView)
async def get_server_a2s_query(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    live: bool = Query(default=False),
) -> A2SQueryView:
    """Return the last A2S snapshot, or run a live query when ``live=true``."""
    server = await require_server_access(db, server_id, current_user)
    cached = (
        await a2s_cache_service.refresh_cached_info(server)
        if live
        else await a2s_cache_service.get_cached_info(int(server.id))
    )
    return _a2s_query_view(
        server,
        cached if isinstance(cached, dict) else None,
        live=live,
    )


@router.get("/{server_id}/monitoring-logs", response_model=MonitoringLogListView)
async def get_server_monitoring_logs(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=50),
) -> MonitoringLogListView:
    """Return recent panel / A2S monitoring log lines from Redis."""
    await require_server_access(db, server_id, current_user)
    raw_logs = await redis_manager.get_monitoring_logs(
        server_id=server_id,
        event_type=event_type,
        limit=limit,
    )
    items: list[MonitoringLogView] = []
    for entry in raw_logs:
        if not isinstance(entry, dict):
            continue
        items.append(
            MonitoringLogView(
                id=str(entry.get("id") or ""),
                event_type=str(entry.get("event_type") or ""),
                status=str(entry.get("status") or ""),
                message=str(entry.get("message") or ""),
                created_at=_parse_optional_datetime(entry.get("created_at")),
            )
        )
    return MonitoringLogListView(items=items)


@router.get("/{server_id}/startup-command", response_model=StartupCommandView)
async def get_server_startup_command(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> StartupCommandView:
    """Return a masked startup-command preview. Does not require SSH."""
    payload = await get_startup_command_legacy(server_id, db, current_user)
    return StartupCommandView(
        startup_command=str(payload.get("startup_command") or ""),
        cs2_command=str(payload.get("cs2_command") or ""),
        session_manager=str(payload.get("session_manager") or "tmux"),
        game_mode_resolved=str(payload.get("game_mode_resolved") or ""),
    )


@router.post("/{server_id}/confirm-deployment", response_model=ConfirmDeploymentView)
async def confirm_server_deployment(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ConfirmDeploymentView:
    """Mark an undeployed server as deployed when remote verification is unavailable."""
    payload = await confirm_deployment_legacy(server_id, db, current_user)
    return ConfirmDeploymentView(
        success=bool(payload.get("success")),
        message=str(payload.get("message") or ""),
        status=str(payload.get("status") or ""),
        last_deployed=payload.get("last_deployed"),
    )
