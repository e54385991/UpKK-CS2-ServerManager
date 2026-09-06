"""Lifecycle-owned global FIFO consumer with a renewable Redis lease."""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from services.plugins import ai_import_store as store
from services.plugins.ai_import_runner import run_job
from services.redis_manager import redis_manager
from services.task_registry import ai_task_registry

logger = logging.getLogger(__name__)
LOCK = store.WORKER_LOCK


class PluginImportWorker:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.boot_time = store.now()

    async def start(self) -> None:
        if self.task is None or self.task.done():
            self.boot_time = store.now()
            self.task = ai_task_registry.create(self.loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def consume(self, token: str) -> None:
        store.worker_lease.set(token)
        await store.reconcile_orphans(self.boot_time)
        while await redis_manager.refresh_lock(LOCK, token, expire=45):
            job = await store.claim_next()
            if job is None:
                await asyncio.sleep(2)
                continue
            runner = ai_task_registry.create(run_job(job))
            try:
                while not runner.done():
                    await asyncio.wait({runner}, timeout=1)
                    current = await store.get_job(job.operation_id)
                    owns_lease = await redis_manager.refresh_lock(LOCK, token, expire=45)
                    if not owns_lease or current is None or current.cancel_requested:
                        runner.cancel()
                        await asyncio.gather(runner, return_exceptions=True)
                        if not owns_lease:
                            return
                await asyncio.gather(runner, return_exceptions=True)
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
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("AI import worker iteration failed")
            await asyncio.sleep(2)


plugin_import_worker = PluginImportWorker()
