"""Passkey (WebAuthn) ceremonies for console login and credential management."""

from .ceremonies import (
    complete_authentication,
    complete_registration,
    delete_credential,
    list_passkeys,
    rename_credential,
    start_authentication,
    start_registration,
)
from .types import MAX_PASSKEYS_PER_USER, PasskeyError, RelyingParty

__all__ = [
    "MAX_PASSKEYS_PER_USER",
    "PasskeyError",
    "RelyingParty",
    "complete_authentication",
    "complete_registration",
    "delete_credential",
    "list_passkeys",
    "rename_credential",
    "start_authentication",
    "start_registration",
]
