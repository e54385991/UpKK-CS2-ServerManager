"""Compare an installed CS2 steam.inf version with the Steam advertised version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from services.steam_api_service import steam_api_service
from services.steam_inf_service import steam_inf_service

InstalledVersionSource = Literal["steam.inf", "database", "unknown"]


@dataclass(frozen=True)
class GameVersionStatus:
    installed_version: Optional[str]
    installed_build_id: Optional[str]
    installed_source: InstalledVersionSource
    advertised_version: Optional[str]
    up_to_date: Optional[bool]
    steam_check_ok: bool
    steam_message: Optional[str]
    steam_error: Optional[str]


def versions_match(observed_version: Optional[str], required_version: Optional[str]) -> bool:
    """Compare dotted steam.inf versions with Steam's numeric fallback format."""
    if not observed_version or not required_version:
        return False
    observed_digits = "".join(character for character in observed_version if character.isdigit())
    required_digits = "".join(character for character in required_version if character.isdigit())
    return bool(observed_digits and observed_digits == required_digits)


async def inspect_game_version(server, *, refresh: bool = False) -> GameVersionStatus:
    """Read steam.inf (cached unless refresh) and ask Steam whether it is current."""
    ok, installed, build_id = await steam_inf_service.get_steam_inf_details(
        server, force_refresh=refresh
    )
    source: InstalledVersionSource = "unknown"
    if ok and installed:
        source = "steam.inf"
    elif getattr(server, "current_game_version", None):
        installed = server.current_game_version
        source = "database"

    steam_ok, result = await steam_api_service.check_version(installed)
    if not steam_ok or not isinstance(result, dict):
        error = None
        if isinstance(result, dict):
            error = result.get("error")
        return GameVersionStatus(
            installed_version=installed,
            installed_build_id=build_id,
            installed_source=source,
            advertised_version=None,
            up_to_date=None,
            steam_check_ok=False,
            steam_message=None,
            steam_error=str(error) if error else "Steam version check failed",
        )

    advertised = result.get("required_version")
    advertised_text = str(advertised) if advertised else None
    up_to_date = result.get("up_to_date")
    if up_to_date is None and installed and advertised_text:
        up_to_date = versions_match(installed, advertised_text)
    elif up_to_date is not None:
        up_to_date = bool(up_to_date)

    return GameVersionStatus(
        installed_version=installed,
        installed_build_id=build_id,
        installed_source=source,
        advertised_version=advertised_text,
        up_to_date=up_to_date,
        steam_check_ok=True,
        steam_message=result.get("message") or None,
        steam_error=None,
    )
