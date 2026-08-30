"""
Steam API Service for CS2 Version Checking and Game Server Account Management
Implements version checking against Steam API for automatic updates
and game server login token (GSLT) generation
"""

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

from modules.http_helper import http_helper
from modules.utils import get_current_time
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)

STEAM_CS2_VERSION_CACHE_KEY = "steam:cs2:uptodate_check"
STEAM_CS2_VERSION_CACHE_TTL = 30 * 60
# UpToDateCheck only includes required_version / message when the queried
# version is behind. Asking for the installed ClientVersion when it is
# already current returns `{up_to_date: true}` with no advertised fields.
STEAM_ADVERTISED_PROBE = "1"
STEAM_UNREACHABLE_HINT = (
    "Cannot reach api.steampowered.com from this panel. In Docker, allow outbound "
    "HTTPS to Steam or set HTTPS_PROXY / HTTP_PROXY on the API container."
)


def steam_version_query(current_version: Optional[str]) -> str:
    """Steam UpToDateCheck wants a numeric ClientVersion, not PatchVersion dots."""
    raw = (current_version or "1").strip() or "1"
    digits = "".join(character for character in raw if character.isdigit())
    return digits or "1"


def advertised_version_from_response(api_response: Dict) -> Optional[str]:
    """Prefer the dotted PatchVersion in Steam's message over the numeric field."""
    message = str(api_response.get("message") or "").strip()
    if "required:" in message.lower():
        candidate = message.split(":")[-1].strip()
        if candidate:
            return candidate
    raw = api_response.get("required_version")
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    return text or None


def resolve_advertised_version(
    advertised: Optional[str],
    *,
    installed: Optional[str],
    up_to_date: Optional[bool],
) -> Optional[str]:
    """Fill a missing advertised version when Steam only said "already current"."""
    text = (advertised or "").strip() or None
    installed_text = (installed or "").strip() or None
    if not text:
        return installed_text if up_to_date and installed_text else None
    if (
        installed_text
        and versions_equivalent(installed_text, text)
        and "." not in text
        and "." in installed_text
    ):
        return installed_text
    return text


def versions_equivalent(observed: Optional[str], required: Optional[str]) -> bool:
    """Compare dotted steam.inf versions with Steam's numeric fallback format."""
    if not observed or not required:
        return False
    observed_digits = "".join(character for character in observed if character.isdigit())
    required_digits = "".join(character for character in required if character.isdigit())
    return bool(observed_digits and observed_digits == required_digits)


def _looks_like_egress_failure(message: Optional[str]) -> bool:
    text = (message or "").lower()
    return any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "connect",
            "name or service",
            "network",
            "proxy",
            "unreachable",
            "ssl",
            "certificate",
        )
    )


class SteamAPIService:
    """Service for checking CS2 version against Steam API and managing game server accounts"""

    # CS2 App ID on Steam
    CS2_APP_ID = 730

    # Steam API endpoint for version checking
    VERSION_CHECK_URL = "https://api.steampowered.com/ISteamApps/UpToDateCheck/v0001/"

    # Steam API endpoint for creating game server account
    CREATE_ACCOUNT_URL = "https://api.steampowered.com/IGameServersService/CreateAccount/v1/"

    @staticmethod
    async def check_version(
        current_version: Optional[str] = None,
        *,
        timeout: int = 10,
        retries: int = 3,
        use_cache: bool = True,
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Check if a CS2 version is up-to-date using Steam API.

        Steam is always queried with a stale probe version so the response
        includes ``required_version``. ``current_version`` is compared locally.
        """
        try:
            if use_cache:
                cached = await redis_manager.get(STEAM_CS2_VERSION_CACHE_KEY)
                if isinstance(cached, dict) and cached.get("required_version"):
                    required = str(cached["required_version"])
                    return True, {
                        "success": True,
                        "up_to_date": versions_equivalent(current_version, required),
                        "required_version": required,
                        "message": cached.get("message") or "",
                        "cached": True,
                    }

            # Always probe with a stale version so Steam returns required_version.
            params = {
                "appid": SteamAPIService.CS2_APP_ID,
                "version": STEAM_ADVERTISED_PROBE,
                "format": "json",
            }

            logger.debug(
                "Checking CS2 version against Steam API: current=%s probe=%s",
                current_version,
                STEAM_ADVERTISED_PROBE,
            )

            # Make async HTTP request using http_helper
            success, data, error_msg = await http_helper.get(
                url=SteamAPIService.VERSION_CHECK_URL,
                params=params,
                timeout=timeout,
                retries=retries,
            )

            if not success:
                logger.error(f"Steam API request failed: {error_msg}")
                hint = (
                    STEAM_UNREACHABLE_HINT
                    if _looks_like_egress_failure(error_msg)
                    else (error_msg or "Failed to connect to Steam API")
                )
                return False, {
                    "success": False,
                    "error": hint,
                }

            # Parse response
            # Expected format:
            # {
            #   "response": {
            #     "success": true,
            #     "up_to_date": false,
            #     "version_is_listable": false,
            #     "required_version": 14125,
            #     "message": "Server version required: 1.41.2.5"
            #   }
            # }

            # Validate data is a dictionary and has expected structure
            if not isinstance(data, dict) or "response" not in data:
                logger.error(f"Unexpected Steam API response format: {data}")
                return False, {"success": False, "error": "Unexpected API response format"}

            api_response = data["response"]
            if not isinstance(api_response, dict):
                logger.error(f"Invalid response structure: {api_response}")
                return False, {"success": False, "error": "Invalid API response structure"}

            message = str(api_response.get("message") or "")
            required_version = advertised_version_from_response(api_response)
            if not required_version and api_response.get("up_to_date") and current_version:
                required_version = str(current_version).strip() or None

            result = {
                "success": True,
                "up_to_date": (
                    versions_equivalent(current_version, required_version)
                    if required_version
                    else bool(api_response.get("up_to_date", False))
                ),
                "required_version": required_version,
                "message": message,
                "raw_response": api_response,
            }

            logger.info(
                "Steam API version check: current=%s, up_to_date=%s, required=%s",
                current_version,
                result["up_to_date"],
                required_version,
            )

            if required_version:
                await redis_manager.set(
                    STEAM_CS2_VERSION_CACHE_KEY,
                    {"required_version": required_version, "message": message},
                    expire=STEAM_CS2_VERSION_CACHE_TTL,
                )

            return True, result
        except Exception as e:
            logger.error(f"Steam API unexpected error: {str(e)}")
            hint = STEAM_UNREACHABLE_HINT if _looks_like_egress_failure(str(e)) else str(e)
            return False, {"success": False, "error": hint}

    @staticmethod
    def parse_version_from_a2s(a2s_version: Optional[str]) -> Optional[str]:
        """
        Parse and normalize version string from A2S query

        Args:
            a2s_version: Version string from A2S query (e.g., "1.41.2.5/14125")

        Returns:
            Normalized version string (e.g., "1.41.2.5") or None
        """
        if not a2s_version:
            return None

        # A2S version can be in format "1.41.2.5/14125" or just "1.41.2.5"
        # Extract the dotted version part
        if "/" in a2s_version:
            version = a2s_version.split("/")[0].strip()
        else:
            version = a2s_version.strip()

        return version if version else None

    @staticmethod
    def should_check_version(last_check: Optional[datetime], interval_hours: int = 1) -> bool:
        """
        Determine if version should be checked based on last check time

        Args:
            last_check: Datetime of last version check
            interval_hours: Hours between checks (default: 1)

        Returns:
            True if version should be checked, False otherwise
        """
        if not last_check:
            return True

        # Calculate time since last check
        now = get_current_time()

        # Make last_check timezone-aware if it's naive
        # Note: This assumes naive timestamps from the database were recorded in local timezone
        # If database contains timestamps from different environments, they should be migrated
        # to timezone-aware format
        if last_check.tzinfo is None:
            # If last_check is naive, assume it was in the local timezone
            # and convert it to timezone-aware
            last_check = last_check.astimezone()

        time_since_check = (now - last_check).total_seconds()

        # Check based on configured interval
        interval_seconds = interval_hours * 3600
        return time_since_check >= interval_seconds

    @staticmethod
    async def create_game_server_account(
        steam_api_key: str, memo: str = ""
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Create a new game server account (GSLT) using Steam API

        Args:
            steam_api_key: User's Steam Web API key
            memo: Optional memo/description for the server account

        Returns:
            Tuple[bool, Optional[Dict]]: (success, result_dict or None)
            result_dict contains:
                - success: bool (API call successful)
                - login_token: str (the generated GSLT token)
                - steamid: str (Steam ID of the game server account)
                - error: str (error message if failed)
        """
        try:
            logger.debug(
                f"Creating game server account for CS2 (appid={SteamAPIService.CS2_APP_ID})"
            )

            # Prepare request parameters
            params = {
                "key": steam_api_key,
                "appid": SteamAPIService.CS2_APP_ID,
                "memo": memo or "CS2 Server",
            }

            # Make HTTP request using the helper
            success, response_data, error_msg = await http_helper.post(
                url=SteamAPIService.CREATE_ACCOUNT_URL, params=params, timeout=15
            )

            if not success:
                logger.error(f"Failed to create game server account: {error_msg}")
                return False, {
                    "success": False,
                    "error": error_msg or "Failed to create game server account",
                }

            # Parse response
            # Expected format:
            # {
            #   "response": {
            #     "steamid": "...",
            #     "login_token": "..."
            #   }
            # }

            if "response" not in response_data:
                logger.error(f"Unexpected Steam API response format: {response_data}")
                return False, {"success": False, "error": "Unexpected API response format"}

            api_response = response_data["response"]

            # Check if login_token exists in response
            if "login_token" not in api_response:
                error_detail = api_response.get("error", "Unknown error")
                logger.error(f"Steam API did not return login_token: {error_detail}")
                return False, {
                    "success": False,
                    "error": f"Failed to generate token: {error_detail}",
                }

            result = {
                "success": True,
                "login_token": api_response["login_token"],
                "steamid": api_response.get("steamid", ""),
                "raw_response": api_response,
            }

            logger.info("Successfully created game server account")

            return True, result

        except Exception as e:
            logger.error(f"Unexpected error creating game server account: {str(e)}")
            return False, {"success": False, "error": f"Unexpected error: {str(e)}"}


# Global instance
steam_api_service = SteamAPIService()
