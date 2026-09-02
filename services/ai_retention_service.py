"""Recovery and retention lifecycle for persisted AI conversations."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta

from sqlmodel import col, select

from modules.database import async_session_maker
from modules.models import AIConversation, AISystemSettings
from modules.utils import get_current_time
from services.ai_orchestrator import cleanup_expired_ai_runs, interrupt_active_ai_runs

logger = logging.getLogger(__name__)
MAX_AI_HISTORY_RETENTION_DAYS = 7
AI_TASK_CLEANUP_INTERVAL_SECONDS = 60
AI_HISTORY_CLEANUP_INTERVAL_TICKS = 24 * 60


class AIRetentionService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        interrupted = await interrupt_active_ai_runs()
        if interrupted:
            logger.warning("Marked %s AI run(s) interrupted after restart", interrupted)
        from services.plugin_diagnostic_service import interrupt_active_plugin_diagnostics

        interrupted_diagnostics = await interrupt_active_plugin_diagnostics()
        if interrupted_diagnostics:
            logger.warning(
                "Marked %s plugin diagnostic run(s) interrupted after restart",
                interrupted_diagnostics,
            )
        await self.cleanup_once()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        history_cleanup_ticks = 0
        while True:
            await asyncio.sleep(AI_TASK_CLEANUP_INTERVAL_SECONDS)
            try:
                history_cleanup_ticks += 1
                if history_cleanup_ticks >= AI_HISTORY_CLEANUP_INTERVAL_TICKS:
                    await self.cleanup_once()
                    history_cleanup_ticks = 0
                else:
                    await self.cleanup_background_tasks_once()
            except Exception:
                logger.exception("AI retention cleanup failed")

    async def cleanup_background_tasks_once(self) -> int:
        async with async_session_maker() as db:
            return await cleanup_expired_ai_runs(db)

    async def cleanup_once(self) -> int:
        async with async_session_maker() as db:
            await cleanup_expired_ai_runs(db)
            settings = await AISystemSettings.get_or_create(db)
            retention_days = min(
                max(1, settings.history_retention_days),
                MAX_AI_HISTORY_RETENTION_DAYS,
            )
            cutoff = get_current_time() - timedelta(days=retention_days)
            result = await db.execute(
                select(AIConversation).where(col(AIConversation.updated_at) < cutoff)
            )
            conversations = list(result.scalars().all())
            for conversation in conversations:
                await db.delete(conversation)
            await db.commit()
            return len(conversations)


ai_retention_service = AIRetentionService()
