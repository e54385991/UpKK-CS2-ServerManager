"""Live, non-secret game information and advisory restart eligibility."""

from dataclasses import dataclass
from datetime import datetime

from modules import Server
from modules.utils import get_current_time
from services.a2s_cache_service import a2s_cache_service
from services.maintenance_lock import maintenance_lock_service
from services.server_operation_hub import server_operation_hub


@dataclass(frozen=True)
class AutomationStatus:
    server_id: int
    status: str
    query_ok: bool
    player_count: int | None
    bot_count: int | None
    human_count: int | None
    max_players: int | None
    version: str | None
    map_name: str | None
    checked_at: datetime
    restart_allowed: bool
    restart_reason: str


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 255 else None


def _text(value: object) -> str | None:
    return " ".join(value.split())[:180] or None if isinstance(value, str) else None


async def read_automation_status(server: Server) -> AutomationStatus:
    """Probe through the shared limiter; cached emptiness is never permission."""
    success, info = await a2s_cache_service.query_live_info(server)
    observed = get_current_time()
    info = info if success and isinstance(info, dict) else {}
    players, bots = _count(info.get("player_count")), _count(info.get("bot_count"))
    humans = (
        players - bots if players is not None and bots is not None and bots <= players else None
    )
    current = await server_operation_hub.get_current(int(server.id))
    busy = await maintenance_lock_service.is_locked(int(server.id))
    reason = "No human players; the Manager must recheck immediately before execution."
    allowed = True
    if server.manual_stop_requested:
        allowed, reason = False, "An administrator requested that this server remain stopped."
    elif current or busy:
        allowed, reason = False, "A server operation or maintenance lock is active."
    elif humans is None:
        allowed, reason = (
            False,
            "Live occupancy is unavailable or invalid; emptiness is unverified.",
        )
    elif humans:
        allowed, reason = False, "Human players are connected."
    return AutomationStatus(
        server_id=int(server.id),
        status=str(getattr(server.status, "value", server.status)),
        query_ok=bool(success),
        player_count=players,
        bot_count=bots,
        human_count=humans,
        max_players=_count(info.get("max_players")),
        version=_text(info.get("version")),
        map_name=_text(info.get("map_name")),
        checked_at=observed,
        restart_allowed=allowed,
        restart_reason=reason,
    )
