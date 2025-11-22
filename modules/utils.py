"""
Utility functions for the CS2 Server Manager
"""
import secrets
import string
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


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
    return ''.join(secrets.choice(alphabet) for _ in range(length))


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
    tz_name = os.environ.get('TZ')
    
    if tz_name:
        try:
            # Use the timezone from TZ environment variable
            from zoneinfo import ZoneInfoNotFoundError
            tz = ZoneInfo(tz_name)
            return datetime.now(tz)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            # If TZ is invalid or not found, fall back to system timezone
            pass
    
    # Use system local timezone
    # datetime.now() without arguments uses local timezone
    # We make it timezone-aware by using astimezone()
    return datetime.now().astimezone()
