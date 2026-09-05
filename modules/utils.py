"""
Utility functions for the CS2 Server Manager
"""

import os
import re
import secrets
import string
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

# Request header carrying the real client address when the panel runs behind a
# reverse proxy. Administrators can change it in system settings; resolution
# lives in ``services.client_ip``.
DEFAULT_CLIENT_IP_HEADER = "X-Forwarded-For"
CLIENT_IP_HEADER_MAX_LENGTH = 64

# Header field names are HTTP tokens; keep the accepted set to what proxies
# actually emit so a typo cannot become a surprising lookup key.
_CLIENT_IP_HEADER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def normalize_client_ip_header(value: Optional[str]) -> Optional[str]:
    """
    Validate a configured source IP header name.

    Args:
        value: Header name from an administrator, or None/blank

    Returns:
        The trimmed header name, or None to use the direct connection address

    Raises:
        ValueError: If the value is not a usable HTTP header name
    """
    if value is None:
        return None
    name = value.strip()
    if not name:
        return None
    if not _CLIENT_IP_HEADER_PATTERN.match(name):
        raise ValueError(
            "client_ip_header must be a header name such as X-Forwarded-For, "
            "or empty to use the direct connection address"
        )
    return name


def generate_api_key(length: int = 64) -> str:
    """
    Generate a secure random API key for server-to-backend communication.

    Args:
        length: Length of the API key (default: 64 characters)

    Returns:
        Randomly generated API key string
    """
    # Use a combination of letters and digits for the API key
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def verify_api_key_format(api_key: str) -> bool:
    """
    Verify that an API key has the correct format.

    Args:
        api_key: API key string to verify

    Returns:
        True if format is valid, False otherwise
    """
    if not api_key or len(api_key) != 64:
        return False

    # Check that it only contains alphanumeric characters
    return all(c in string.ascii_letters + string.digits for c in api_key)


def get_current_time() -> datetime:
    """
    Get the current time using system timezone or TZ environment variable.

    This function respects the TZ environment variable if set, otherwise uses
    the system's local timezone. This replaces hardcoded UTC usage.

    Returns:
        Timezone-aware datetime object representing the current time
    """
    # Check if TZ environment variable is set
    tz_name = os.environ.get("TZ")

    if tz_name:
        try:
            # Use the timezone from TZ environment variable
            from zoneinfo import ZoneInfoNotFoundError

            tz = ZoneInfo(tz_name)
            return datetime.now(tz)
        except ZoneInfoNotFoundError, ValueError, KeyError:
            # If TZ is invalid or not found, fall back to system timezone
            pass

    # Use system local timezone
    # datetime.now() without arguments uses local timezone
    # We make it timezone-aware by using astimezone()
    return datetime.now().astimezone()
