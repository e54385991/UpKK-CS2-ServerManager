"""Small infrastructure protocols used by application services.

The protocols keep feature code dependent on capabilities rather than concrete
Redis, HTTP, or SSH implementations. Concrete adapters remain backward
compatible and can be replaced in tests through :mod:`services.container`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


class HTTPTransport(Protocol):
    async def make_request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class CoordinationStore(Protocol):
    async def get(self, key: str) -> Any | None: ...

    async def set(self, key: str, value: Any, expire: int = 300) -> bool: ...

    async def delete(self, key: str) -> bool: ...


class SSHLeaseProvider(Protocol):
    def lease(self, *args: Any, **kwargs: Any) -> AbstractAsyncContextManager[Any]: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[Any]]
Cleanup = Callable[[], Awaitable[object]]
AsyncStream = AsyncIterator[Any]


__all__ = [
    "AsyncStream",
    "Cleanup",
    "CoordinationStore",
    "HTTPTransport",
    "SSHLeaseProvider",
    "SessionFactory",
]
