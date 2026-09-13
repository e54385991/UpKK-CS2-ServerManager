"""Discord status-card formatting and panel source loading."""

from __future__ import annotations

import asyncio
from datetime import datetime

from modules.models import Server
from services.compat import LateBoundModule

host = LateBoundModule("services.discord_bot_manager")


def format_panel_update_age(timestamp: object | None) -> str | None:
    """Mirror the panel overview `formatTimestamp` relative age."""

    if timestamp in (None, ""):
        return None
    raw = str(timestamp).strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = host.get_current_time()
    if value.tzinfo is None:
        value = value.replace(tzinfo=now.tzinfo)
    else:
        now = now.astimezone(value.tzinfo)
    diff_sec = int((now - value).total_seconds())
    if diff_sec < 0:
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if diff_sec < 60:
        return f"{diff_sec}s ago"
    if diff_sec < 3600:
        return f"{diff_sec // 60}m ago"
    if diff_sec < 86400:
        return f"{diff_sec // 3600}h ago"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _status_unknown(locale: object) -> str:
    return host.menu_text(locale, "status_unknown")


def _real_status_text(value: object | None, locale: object) -> str:
    if value is None:
        return _status_unknown(locale)
    text = str(value).strip()
    return text or _status_unknown(locale)


def _format_disk_gb(value: object | None, locale: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _status_unknown(locale)
    return f"{float(value):.2f} GB"


def _format_disk_percent(value: object | None, locale: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _status_unknown(locale)
    return f"{float(value):.2f}%"


def _format_latency_ms(value: object | None, locale: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _status_unknown(locale)
    return f"{max(0, int(value))}ms"


def status_card_fields(
    server: Server,
    *,
    a2s_ok: bool,
    info: dict | None,
    locale: object = "en-US",
    response_time_ms: object | None = None,
    last_updated: object | None = None,
    disk_info: dict | None = None,
) -> list[tuple[str, str]]:
    """Build the public status card from the same A2S/disk fields as the panel."""

    _ = server
    unknown = _status_unknown(locale)
    payload = info if a2s_ok and isinstance(info, dict) else {}
    player_count = payload.get("player_count")
    max_players = payload.get("max_players")
    players = (
        f"{player_count}/{max_players}"
        if isinstance(player_count, int) and isinstance(max_players, int)
        else unknown
    )
    disk = disk_info if isinstance(disk_info, dict) else {}
    return [
        (
            host.menu_text(locale, "status_online_state"),
            host.menu_text(locale, "status_online" if a2s_ok else "status_offline"),
        ),
        (
            host.menu_text(locale, "status_server_name"),
            _real_status_text(payload.get("server_name"), locale),
        ),
        (host.menu_text(locale, "status_map"), _real_status_text(payload.get("map_name"), locale)),
        (host.menu_text(locale, "status_players"), players),
        (host.menu_text(locale, "status_latency"), _format_latency_ms(response_time_ms, locale)),
        (
            host.menu_text(locale, "status_cs2_version"),
            _real_status_text(payload.get("version"), locale),
        ),
        (
            host.menu_text(locale, "status_updated"),
            format_panel_update_age(last_updated) or unknown,
        ),
        (
            host.menu_text(locale, "status_disk_directory"),
            _format_disk_gb(disk.get("used_gb"), locale),
        ),
        (
            host.menu_text(locale, "status_disk_total"),
            _format_disk_gb(disk.get("total_gb"), locale),
        ),
        (
            host.menu_text(locale, "status_disk_usage"),
            _format_disk_percent(disk.get("used_percent"), locale),
        ),
    ]


async def load_panel_status_sources(server: Server) -> dict:
    """Reuse the panel A2S cache and disk-space cache.

    Live A2S is used only when the panel cache is missing. Disk space stays
    cache-only so Discord never invents values or opens a second SSH path.
    """

    from services.a2s_cache_service import a2s_cache_service
    from services.a2s_query import a2s_service
    from services.disk_space_service import disk_space_service

    cached = await a2s_cache_service.get_cached_info(server.id)
    if isinstance(cached, dict):
        ok = bool(cached.get("success") and cached.get("server_info"))
        info = cached.get("server_info") if ok else None
        response_time_ms = cached.get("response_time_ms")
        last_updated = cached.get("last_updated") or cached.get("timestamp")
    else:
        query_host = server.a2s_query_host or server.host
        port = server.a2s_query_port or server.game_port
        loop = asyncio.get_running_loop()
        started = loop.time()
        ok, info = await a2s_service.query_server_info(query_host, port, timeout=5)
        response_time_ms = int((loop.time() - started) * 1000) if ok else None
        last_updated = host.get_current_time().isoformat() if ok else None

    disk_ok, disk_info = await disk_space_service.get_disk_space(server, cache_only=True)
    return {
        "a2s_ok": ok,
        "info": info if ok else None,
        "response_time_ms": response_time_ms,
        "last_updated": last_updated,
        "disk_info": disk_info if disk_ok else None,
    }
