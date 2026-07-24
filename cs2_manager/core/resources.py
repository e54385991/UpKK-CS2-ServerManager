"""Protocols for resources owned by one application instance."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


class AsyncCloseable(Protocol):
    async def close(self) -> object: ...


class DatabaseResourceProtocol(Protocol):
    engine: AsyncEngine
    session_factory: Callable[[], AsyncSession]

    async def ping(self) -> bool: ...

    async def close(self) -> object: ...


class RedisResourceProtocol(Protocol):
    async def ping(self) -> bool: ...

    async def delete_by_pattern(self, pattern: str, count: int = 100) -> int: ...

    async def close(self) -> object: ...


class HTTPResourceProtocol(AsyncCloseable, Protocol):
    pass


class SSHConnectionPoolProtocol(Protocol):
    async def start_cleanup(self) -> object: ...

    async def stop_cleanup(self) -> object: ...

    async def close_all(self) -> object: ...


class TaskSupervisorProtocol(Protocol):
    def start(self) -> None: ...

    def create(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
    ) -> Any: ...

    async def shutdown(self) -> None: ...
