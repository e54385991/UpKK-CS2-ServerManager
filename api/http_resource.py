"""Request-scoped access to the lifespan-owned outbound HTTP adapter."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

import httpx
from fastapi import HTTPException, Request, status


class ApplicationHTTP(Protocol):
    """Operations used by HTTP-backed API integrations."""

    async def get(self, url: str, **kwargs: Any) -> tuple[bool, Any, str | None]: ...

    async def post(self, url: str, **kwargs: Any) -> tuple[bool, Any, str | None]: ...

    def borrow_client(self) -> AbstractAsyncContextManager[httpx.AsyncClient]: ...


def as_application_http(resource: object) -> ApplicationHTTP | None:
    """Return a compatible adapter, excluding closed or incomplete resources."""
    if resource is None or getattr(resource, "is_closed", False) is True:
        return None
    required_operations = ("get", "post", "borrow_client")
    if not all(callable(getattr(resource, operation, None)) for operation in required_operations):
        return None
    return cast(ApplicationHTTP, resource)


def resolve_application_http(request: Request) -> ApplicationHTTP:
    """Resolve only the HTTP adapter owned by the current FastAPI app.

    Outbound integrations fail closed instead of falling back to the
    process-global compatibility client. This prevents isolated factory apps
    from sharing connection pools or credentials accidentally.
    """
    container = getattr(request.app.state, "container", None)
    resource = as_application_http(getattr(container, "http", None))
    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Outbound HTTP client is unavailable",
        )
    return resource
