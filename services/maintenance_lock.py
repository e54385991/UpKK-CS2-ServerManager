"""Per-server operation locks shared across tasks and application processes."""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator, Dict, Protocol

from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)

MAINTENANCE_LOCK_SERVICE_KEY = "maintenance_lock"


class MaintenanceLockRedisAdapter(Protocol):
    """Minimal Redis contract required for distributed operation locks."""

    def acquire_lock(
        self,
        key: str,
        token: str,
        expire: int,
    ) -> Awaitable[bool | None]: ...

    def is_lock_held(self, key: str) -> Awaitable[bool | None]: ...

    def refresh_lock(self, key: str, token: str, expire: int) -> Awaitable[bool]: ...

    def release_lock(self, key: str, token: str) -> Awaitable[bool]: ...


class OperationBusyError(RuntimeError):
    """Raised when another mutating operation already owns a server lock."""


class OperationCoordinationUnavailable(RuntimeError):
    """Raised when distributed serialization cannot be guaranteed."""


class _MaintenanceLockHandle:
    """Compatibility handle supporting Lock-style and context-manager usage."""

    def __init__(self, service, server_id, operation, wait, wait_timeout, ttl) -> None:
        self._service = service
        self._server_id = server_id
        self._context = service._hold(server_id, operation, wait, wait_timeout, ttl)
        self._entered = False

    def locked(self) -> bool:
        lock = self._service._locks.get(self._server_id)
        return bool(lock and lock.locked())

    async def acquire(self) -> bool:
        if not self._entered:
            await self._context.__aenter__()
            self._entered = True
        return True

    def release(self) -> None:
        if self._entered:
            self._entered = False
            asyncio.create_task(self._context.__aexit__(None, None, None))

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if self._entered:
            self._entered = False
            await self._context.__aexit__(exc_type, exc, traceback)


class MaintenanceLockService:
    def __init__(
        self,
        redis_adapter: MaintenanceLockRedisAdapter = redis_manager,
    ) -> None:
        self._redis = redis_adapter
        self._locks: Dict[int, asyncio.Lock] = {}

    @property
    def redis_adapter(self) -> MaintenanceLockRedisAdapter:
        """Expose the bound adapter for diagnostics and isolation tests."""
        return self._redis

    def get(
        self,
        server_id: int,
        *,
        operation: str = "maintenance",
        wait: bool = True,
        wait_timeout: float = 30.0,
        ttl: int = 900,
    ):
        return _MaintenanceLockHandle(self, server_id, operation, wait, wait_timeout, ttl)

    async def is_locked(self, server_id: int) -> bool:
        local_lock = self._locks.get(server_id)
        if local_lock is not None and local_lock.locked():
            return True
        try:
            held = await self._redis.is_lock_held(f"server_operation_lock:{server_id}")
        except Exception as exc:
            raise OperationCoordinationUnavailable(
                "Coordination storage is unavailable; refusing destructive operation"
            ) from exc
        if held is None:
            raise OperationCoordinationUnavailable(
                "Coordination storage is unavailable; refusing destructive operation"
            )
        return held

    @asynccontextmanager
    async def _hold(
        self,
        server_id: int,
        operation: str,
        wait: bool,
        wait_timeout: float,
        ttl: int,
    ) -> AsyncIterator[None]:
        local_lock = self._locks.setdefault(server_id, asyncio.Lock())
        if not wait and local_lock.locked():
            raise OperationBusyError(f"Server {server_id} already has an operation in progress")

        try:
            if wait:
                await asyncio.wait_for(local_lock.acquire(), timeout=wait_timeout)
            else:
                await local_lock.acquire()
        except asyncio.TimeoutError as exc:
            raise OperationBusyError(f"Timed out waiting for server {server_id}") from exc

        key = f"server_operation_lock:{server_id}"
        token = f"{uuid.uuid4().hex}:{operation}"
        distributed_acquired = False
        renew_task = None
        try:
            deadline = time.monotonic() + wait_timeout
            while True:
                try:
                    acquired = await self._redis.acquire_lock(key, token, ttl)
                except Exception as exc:
                    raise OperationCoordinationUnavailable(
                        "Coordination storage is unavailable; refusing destructive operation"
                    ) from exc
                if acquired is None:
                    raise OperationCoordinationUnavailable(
                        "Coordination storage is unavailable; refusing destructive operation"
                    )
                if acquired:
                    distributed_acquired = True
                    break
                if not wait or time.monotonic() >= deadline:
                    raise OperationBusyError(
                        f"Server {server_id} already has an operation in progress"
                    )
                await asyncio.sleep(0.25)

            if distributed_acquired:
                renew_task = asyncio.create_task(
                    self._renew(key, token, ttl, asyncio.current_task())
                )
            yield
        finally:
            if renew_task is not None:
                renew_task.cancel()
                with suppress(asyncio.CancelledError):
                    await renew_task
            if distributed_acquired:
                try:
                    await self._redis.release_lock(key, token)
                except Exception:
                    logger.error("Failed to release distributed operation lock: %s", key)
            local_lock.release()

    async def _renew(
        self,
        key: str,
        token: str,
        ttl: int,
        owner_task: asyncio.Task | None,
    ) -> None:
        while True:
            await asyncio.sleep(max(1, ttl // 3))
            try:
                refreshed = await self._redis.refresh_lock(key, token, ttl)
            except Exception:
                refreshed = False
            if not refreshed:
                logger.error("Lost distributed server operation lock: %s", key)
                # Continuing a destructive operation after losing its global
                # lease can corrupt state. Cancellation is the fail-closed
                # boundary; command handlers already perform cleanup in finally.
                if owner_task is not None:
                    owner_task.cancel("Distributed operation lock was lost")
                return


maintenance_lock_service = MaintenanceLockService(redis_manager)
