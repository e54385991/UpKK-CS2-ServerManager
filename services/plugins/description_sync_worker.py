"""Lifecycle-owned global FIFO worker for marketplace description syncs."""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import uuid4

from services.plugins import description_sync_store as store
from services.plugins.description_sync_runner import run_job
from services.redis_manager import redis_manager
from services.task_registry import plugin_description_task_registry

logger = logging.getLogger(__name__)
LOCK = store.WORKER_LOCK


class DescriptionSyncWorker:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = plugin_description_task_registry.create(self.loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    @staticmethod
    async def _wait_until(job_id: str, retry_at: int) -> bool:
        while True:
            remaining = retry_at - int(time.time())
            if remaining <= 0:
                return True
            await asyncio.sleep(min(remaining, 5))
            current = await store.get_job(job_id)
            if current is None or current.status != "waiting":
                return False

    async def consume(self, token: str) -> None:
        store.worker_lease.set(token)
        await store.reconcile_orphans()
        while await redis_manager.refresh_lock(LOCK, token, expire=45):
            job = await store.claim_next()
            if job is None:
                await asyncio.sleep(2)
                continue
            if job.status == "waiting" and job.retry_at is not None:
                if not await self._wait_until(job.operation_id, job.retry_at):
                    continue
                if not await redis_manager.refresh_lock(LOCK, token, expire=45):
                    return
                continue
            if job.status not in ("running", "queued"):
                continue

            runner = plugin_description_task_registry.create(run_job(job))
            try:
                while not runner.done():
                    await asyncio.wait({runner}, timeout=1)
                    owns_lease = await redis_manager.refresh_lock(LOCK, token, expire=45)
                    if not owns_lease:
                        runner.cancel()
                        await asyncio.gather(runner, return_exceptions=True)
                        return
            finally:
                if not runner.done():
                    runner.cancel()
                    await asyncio.gather(runner, return_exceptions=True)

    async def loop(self) -> None:
        while True:
            token = str(uuid4())
            try:
                if await redis_manager.acquire_lock(LOCK, token, expire=45):
                    try:
                        await self.consume(token)
                    finally:
                        await redis_manager.release_lock(LOCK, token)
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Description sync worker loop failed")
                await asyncio.sleep(5)


description_sync_worker = DescriptionSyncWorker()
