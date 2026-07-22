"""Small Redis-backed limits for public CPU- and network-heavy endpoints."""
import hashlib

from fastapi import HTTPException, Request, status

from services.redis_manager import redis_manager


def _client_address(request: Request) -> str:
    # Do not trust X-Forwarded-For unless the ASGI server is configured to trust its proxy.
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(
    request: Request,
    scope: str,
    *,
    limit: int,
    window: int,
    identity: str | None = None,
) -> None:
    raw_identity = f"{_client_address(request)}:{identity or ''}"
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
