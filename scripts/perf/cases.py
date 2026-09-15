"""Fixed measurement matrix for this performance round."""

from __future__ import annotations

from typing import Any

from scripts.perf.profiles import FLEETS, MARKETS, OVERVIEW_SERVER_LIMIT

PRIMARY_FLEETS = ("fleet-10", "fleet-100", "fleet-500")
COMPATIBILITY_FLEETS = ("fleet-1000", "fleet-1001")
MARKET_SIZES = ("market-100", "market-1000", "market-10000")
HISTORY_KINDS = ("empty", "daily", "max")
ACTORS = ("admin", "member", "tenant_a", "tenant_b")
SINGLE_ROUTES = ("inbox", "overview", "market", "servers")
BROWSER_ROUTES = (
    "login",
    "overview",
    "servers",
    "activity-tray",
    "plugins-install",
    "assistant",
    "files",
)
BROWSER_LOCALES = ("en-US", "zh-CN")
BROWSER_WIDTHS = (390, 1440)


def compatibility_cases() -> list[dict[str, Any]]:
    """Overview cap cases stay out of the 10/100/500 inbox series."""
    rows: list[dict[str, Any]] = []
    for name in COMPATIBILITY_FLEETS:
        fleet = FLEETS[name]
        rows.append(
            {
                "fleet": name,
                "servers": fleet.servers,
                "overview_counted": min(fleet.servers, OVERVIEW_SERVER_LIMIT),
                "purpose": "existing overview 1000-row cap",
            }
        )
    return rows


def measurement_matrix() -> dict[str, Any]:
    return {
        "primary_fleets": [
            {
                "fleet": name,
                "servers": FLEETS[name].servers,
                "sessions": FLEETS[name].online_users,
            }
            for name in PRIMARY_FLEETS
        ],
        "compatibility_fleets": compatibility_cases(),
        "markets": [{"market": name, "plugins": MARKETS[name].plugins} for name in MARKET_SIZES],
        "history": list(HISTORY_KINDS),
        "actors": list(ACTORS),
        "single_routes": list(SINGLE_ROUTES),
        "mixed_routes": list(SINGLE_ROUTES),
        "realtime_sessions": FLEETS["fleet-500"].online_users,
        "browser_routes": list(BROWSER_ROUTES),
        "browser_locales": list(BROWSER_LOCALES),
        "browser_widths": list(BROWSER_WIDTHS),
    }
