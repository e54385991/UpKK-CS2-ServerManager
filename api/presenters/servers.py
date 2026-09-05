"""Safe server projections used by the versioned HTTP API."""

from __future__ import annotations

from typing import Literal, TypedDict

from api.contracts.v1.server import ServerDetail, ServerSummary
from modules import Server, User
from services.server_compatibility import DEFAULT_EXECSTACK_TARGETS, effective_clear_execstack
from services.ssh_connection_pool import ssh_connection_pool


class ServerPoolFields(TypedDict):
    """Typed, non-secret connection-pool fields appended to a detail view."""

    ssh_pooled: bool
    ssh_in_use: bool
    ssh_active_leases: int
    ssh_idle_seconds: float | None


def _server_id(server: Server) -> int:
    value = server.id
    if value is None:
        raise ValueError("Cannot present a server without an id")
    return value


def to_summary(server: Server, owner: User | None = None) -> ServerSummary:
    """Map a server ORM object to a non-secret list projection."""
    os_id = getattr(server, "os_id", None) or None
    os_version = getattr(server, "os_version", None) or None
    override = getattr(server, "clear_execstack_override", None)
    return ServerSummary(
        id=_server_id(server),
        name=server.name,
        host=server.host,
        game_port=server.game_port,
        ssh_user=server.ssh_user,
        status=server.status,
        description=server.description,
        default_map=server.default_map,
        max_players=server.max_players,
        owner_id=owner.id if owner is not None else None,
        owner_username=owner.username if owner is not None else None,
        owner_is_admin=bool(owner.is_admin) if owner is not None else None,
        use_panel_proxy=bool(getattr(server, "use_panel_proxy", False)),
        github_proxy=getattr(server, "github_proxy", None) or None,
        is_ssh_down=bool(getattr(server, "is_ssh_down", False)),
        ssh_health_status=str(getattr(server, "ssh_health_status", None) or "unknown"),
        consecutive_ssh_failures=int(getattr(server, "consecutive_ssh_failures", 0) or 0),
        ssh_health_failure_threshold=int(getattr(server, "ssh_health_failure_threshold", 84) or 84),
        ssh_health_check_interval_hours=int(
            getattr(server, "ssh_health_check_interval_hours", 2) or 2
        ),
        last_ssh_health_check=getattr(server, "last_ssh_health_check", None),
        os_id=os_id,
        os_version=os_version,
        clear_execstack_override=override,
        clear_execstack_effective=effective_clear_execstack(server),
    )


async def _ssh_pool_fields(server: Server) -> ServerPoolFields:
    try:
        info = await ssh_connection_pool.get_connection_info(server)
    except Exception:
        return {
            "ssh_pooled": False,
            "ssh_in_use": False,
            "ssh_active_leases": 0,
            "ssh_idle_seconds": None,
        }
    idle = info.get("idle_time")
    leases = info.get("active_leases")
    if leases is None:
        leases = 1 if info.get("in_use") else 0
    return {
        "ssh_pooled": bool(info.get("connected")),
        "ssh_in_use": bool(info.get("in_use")),
        "ssh_active_leases": int(leases),
        "ssh_idle_seconds": float(idle) if idle is not None else None,
    }


async def to_detail(server: Server) -> ServerDetail:
    """Map a server ORM object to a non-secret detail projection."""
    raw_session_manager = getattr(server, "session_manager", "tmux")
    session_manager: Literal["screen", "tmux"]
    if isinstance(raw_session_manager, str) and raw_session_manager == "screen":
        session_manager = "screen"
    else:
        session_manager = "tmux"
    created_at = server.created_at
    updated_at = server.updated_at
    if created_at is None or updated_at is None:
        raise ValueError("Cannot present a server without timestamps")
    pool = await _ssh_pool_fields(server)
    os_id = getattr(server, "os_id", None) or None
    os_version = getattr(server, "os_version", None) or None
    override = getattr(server, "clear_execstack_override", None)
    raw_targets = getattr(server, "execstack_fix_targets", None)
    targets: list[str] = [str(value) for value in (raw_targets or DEFAULT_EXECSTACK_TARGETS)]
    return ServerDetail(
        id=_server_id(server),
        name=server.name,
        host=server.host,
        game_port=server.game_port,
        ssh_user=server.ssh_user,
        status=server.status,
        description=server.description,
        default_map=server.default_map,
        max_players=server.max_players,
        ssh_port=server.ssh_port,
        game_directory=server.game_directory,
        game_mode=server.game_mode,
        game_type=server.game_type,
        server_name=getattr(server, "server_name", None) or server.name,
        session_manager=session_manager,
        enable_panel_monitoring=bool(getattr(server, "enable_panel_monitoring", False)),
        monitor_interval_seconds=int(getattr(server, "monitor_interval_seconds", 60) or 60),
        auto_restart_on_crash=bool(getattr(server, "auto_restart_on_crash", True)),
        enable_a2s_monitoring=bool(getattr(server, "enable_a2s_monitoring", False)),
        a2s_failure_threshold=int(getattr(server, "a2s_failure_threshold", 3) or 3),
        a2s_check_interval_seconds=int(getattr(server, "a2s_check_interval_seconds", 60) or 60),
        a2s_query_host=getattr(server, "a2s_query_host", None) or None,
        a2s_query_port=getattr(server, "a2s_query_port", None),
        enable_auto_update=bool(getattr(server, "enable_auto_update", True)),
        tv_enable=bool(getattr(server, "tv_enable", False)),
        is_ssh_down=bool(getattr(server, "is_ssh_down", False)),
        ssh_health_status=str(getattr(server, "ssh_health_status", None) or "unknown"),
        consecutive_ssh_failures=int(getattr(server, "consecutive_ssh_failures", 0) or 0),
        ssh_health_failure_threshold=int(getattr(server, "ssh_health_failure_threshold", 84) or 84),
        ssh_health_check_interval_hours=int(
            getattr(server, "ssh_health_check_interval_hours", 2) or 2
        ),
        last_ssh_health_check=getattr(server, "last_ssh_health_check", None),
        last_ssh_success=getattr(server, "last_ssh_success", None),
        created_at=created_at,
        updated_at=updated_at,
        last_deployed=server.last_deployed,
        apt_mirror=getattr(server, "apt_mirror", None),
        additional_parameters=getattr(server, "additional_parameters", None) or None,
        has_sudo_password=bool(getattr(server, "sudo_password", None)),
        use_panel_proxy=bool(getattr(server, "use_panel_proxy", False)),
        github_proxy=getattr(server, "github_proxy", None) or None,
        os_id=os_id,
        os_version=os_version,
        clear_execstack_override=override,
        clear_execstack_effective=effective_clear_execstack(server),
        execstack_fix_on_restart=bool(getattr(server, "execstack_fix_on_restart", True)),
        execstack_fix_on_framework=bool(getattr(server, "execstack_fix_on_framework", True)),
        execstack_fix_on_game_update=bool(getattr(server, "execstack_fix_on_game_update", True)),
        execstack_fix_targets=targets,
        **pool,
    )


__all__ = ["to_detail", "to_summary"]
