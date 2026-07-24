"""Infrastructure adapters used by application containers."""

from .credentials import (
    CredentialCipher,
    CredentialShadow,
    EncryptedText,
    credential_aad,
    credential_shadow_update_values,
    decrypt_credential_shadow,
    encrypt_credential_shadow,
    hash_token,
    register_credential_shadows,
)
from .database import DatabaseResource, LegacyDatabaseResource, UnitOfWork

__all__ = [
    "CredentialCipher",
    "CredentialShadow",
    "DatabaseResource",
    "EncryptedText",
    "LegacyDatabaseResource",
    "UnitOfWork",
    "credential_aad",
    "credential_shadow_update_values",
    "decrypt_credential_shadow",
    "encrypt_credential_shadow",
    "hash_token",
    "register_credential_shadows",
]
