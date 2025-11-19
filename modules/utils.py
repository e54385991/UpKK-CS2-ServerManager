"""
Utility functions for the CS2 Server Manager
"""
import secrets
import string


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
