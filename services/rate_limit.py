"""Small Redis-backed limits for public CPU- and network-heavy endpoints."""

import hashlib

from fastapi import HTTPException, Request, status

from services.client_ip import UNKNOWN_CLIENT_ADDRESS, request_client_ip
from services.redis_manager import redis_manager


async def _client_address(request: Request) -> str:
    # Which header (if any) may be trusted is an administrator policy: behind a
    # reverse proxy the socket peer is the proxy, so every visitor would
    # otherwise share one bucket.
    return await request_client_ip(request) or UNKNOWN_CLIENT_ADDRESS


async def enforce_rate_limit(
    request: Request,
    scope: str,
    *,
    limit: int,
    window: int,
    identity: str | None = None,
) -> None:
    raw_identity = f"{await _client_address(request)}:{identity or ''}"
    digest = hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()
    allowed, retry_after = await redis_manager.hit_rate_limit(
        f"rate_limit:{scope}:{digest}",
        limit,
        window,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
