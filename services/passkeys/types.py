"""Transport-independent passkey constants and errors."""

from __future__ import annotations

from dataclasses import dataclass

MAX_PASSKEYS_PER_USER = 16
CHALLENGE_TTL_SECONDS = 120
RP_NAME = "CS2 Server Manager"
GENERIC_LOGIN_FAILURE = "passkey_verification_failed"


class PasskeyError(Exception):
    """Domain failure mapped to an HTTP status by the v1 route layer."""

    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class RelyingParty:
    origin: str
    rp_id: str
    rp_name: str = RP_NAME
