"""One-time WebAuthn challenges stored in Redis."""

from __future__ import annotations

import json
from typing import Any

from webauthn.helpers import base64url_to_bytes

from services.passkeys.types import CHALLENGE_TTL_SECONDS, PasskeyError
from services.redis_manager import redis_manager


def challenge_key(challenge_b64: str) -> str:
    return f"webauthn:challenge:{challenge_b64}"


def challenge_b64_from_credential(credential: dict[str, Any]) -> str:
    response = credential.get("response")
    raw = response.get("clientDataJSON") if isinstance(response, dict) else None
    if not isinstance(raw, str) or not raw:
        raise PasskeyError("passkey_verification_failed", 401)
    try:
        payload = json.loads(base64url_to_bytes(raw))
    except ValueError, TypeError, json.JSONDecodeError:
        raise PasskeyError("passkey_verification_failed", 401) from None
    challenge = payload.get("challenge") if isinstance(payload, dict) else None
    if not isinstance(challenge, str) or not challenge:
        raise PasskeyError("passkey_verification_failed", 401)
    return challenge


async def store_challenge(
    challenge_b64: str,
    *,
    ceremony: str,
    origin: str,
    rp_id: str,
    user_id: int | None,
) -> None:
    stored = await redis_manager.set(
        challenge_key(challenge_b64),
        {
            "type": ceremony,
            "origin": origin,
            "rp_id": rp_id,
            "user_id": user_id,
            "challenge": challenge_b64,
        },
        expire=CHALLENGE_TTL_SECONDS,
    )
    if not stored:
        raise PasskeyError("passkey_challenge_store_failed", 503)


async def consume_challenge(challenge_b64: str) -> dict[str, Any]:
    payload = await redis_manager.getdel(challenge_key(challenge_b64))
    if not isinstance(payload, dict):
        raise PasskeyError("passkey_challenge_expired")
    return payload
