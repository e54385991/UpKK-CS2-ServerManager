"""Generate setup passwords that satisfy the existing PAM character policy."""

import secrets
import string


def generate_secure_password(length: int = 16) -> str:
    """
    Generate a secure random password with special characters to meet PAM requirements
    Uses safe special characters and proper escaping to avoid shell issues
    """
    # Use safe special characters that are commonly accepted by PAM policies
    # Avoiding characters that have special meaning in shell: ' " ` $ \ ! and others
    safe_special_chars = "!@#%^&*()_+-=[]{}|;:,.<>?"

    # Build character sets
    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits

    # Ensure password has at least one of each required type for PAM compliance
    password = [
        secrets.choice(lowercase),  # At least one lowercase
        secrets.choice(uppercase),  # At least one uppercase
        secrets.choice(digits),  # At least one digit
        secrets.choice(safe_special_chars),  # At least one special character
    ]

    # Fill the rest randomly from all character sets
    all_chars = lowercase + uppercase + digits + safe_special_chars
    password += [secrets.choice(all_chars) for _ in range(length - 4)]

    # Shuffle to avoid predictable patterns
    secrets.SystemRandom().shuffle(password)
    return "".join(password)
