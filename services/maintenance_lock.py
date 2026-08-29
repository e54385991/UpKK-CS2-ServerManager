"""Per-server operation locks shared across tasks and application processes."""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator, MutableMapping
from weakref import WeakValueDictionary

from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)


class OperationBusyError(RuntimeError):
    """Raised when another mutating operation already owns a server lock."""


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
    def __init__(self) -> None:
        # Active holders and waiters keep their lock strongly referenced. Once
        # the final operation exits, the weak map forgets that server ID instead
        # of growing for the lifetime of the process.
        self._locks: MutableMapping[int, asyncio.Lock] = WeakValueDictionary()

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
        return bool(await redis_manager.is_lock_held(f"server_operation_lock:{server_id}"))

    async def clear_stale_server_lock(
        self,
        server_id: int,
        *,
        operation_prefixes: tuple[str, ...],
    ) -> bool:
        """Clear an orphaned AI lock without touching live local operations.

        Redis locks can outlive a crashed worker. Only locks whose operation
        token matches the caller's explicitly allowed prefixes are eligible;
        regular API, scheduled, and auto-update locks remain protected.
        """
        local_lock = self._locks.get(server_id)
        if local_lock is not None and local_lock.locked():
            return False
        key = f"server_operation_lock:{server_id}"
        token = await redis_manager.get_lock_token(key)
        if not token:
            return False
        # Tokens are generated as ``<uuid>:<operation>`` and operations may
        # themselves contain colons (for example ``ai:restart``).
        operation = str(token).split(":", 1)[1] if ":" in str(token) else ""
        if not any(
            operation == prefix or (prefix.endswith(":") and operation.startswith(prefix))
            for prefix in operation_prefixes
        ):
            return False
        released = await redis_manager.release_lock(key, token)
        if released:
            logger.warning("Cleared stale AI server operation lock for server %s", server_id)
        return released

    async def force_release_server_lock(
        self, server_id: int, *, ignore_local: bool = False
    ) -> bool:
        """Release a lock owned by work recovered as interrupted at startup."""
        local_lock = self._locks.get(server_id)
        if not ignore_local and local_lock is not None and local_lock.locked():
            return False
        key = f"server_operation_lock:{server_id}"
        token = await redis_manager.get_lock_token(key)
        if not token:
            return False
        return await redis_manager.release_lock(key, token)

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
                acquired = await redis_manager.acquire_lock(key, token, ttl)
                if acquired is None:
                    logger.warning(
                        "Redis unavailable; server %s operation %s is protected in-process only",
                        server_id,
                        operation,
                    )
                    break
                if acquired:
                    distributed_acquired = True
                    break
                if not wait or time.monotonic() >= deadline:
                    raise OperationBusyError(
                        f"Server {server_id} already has an operation in progress"
                    )
                await asyncio.sleep(0.25)

            if distributed_acquired:
                renew_task = asyncio.create_task(self._renew(key, token, ttl))
            yield
        finally:
            if renew_task is not None:
                renew_task.cancel()
                with suppress(asyncio.CancelledError):
                    await renew_task
            if distributed_acquired:
                await redis_manager.release_lock(key, token)
            local_lock.release()

    @staticmethod
    async def _renew(key: str, token: str, ttl: int) -> None:
        while True:
            await asyncio.sleep(max(1, ttl // 3))
            if not await redis_manager.refresh_lock(key, token, ttl):
                logger.error("Lost distributed server operation lock: %s", key)
                return


maintenance_lock_service = MaintenanceLockService()
