"""Recovery and retention lifecycle for persisted AI conversations."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta

from sqlmodel import select

from modules.database import async_session_maker
from modules.models import AIConversation, AISystemSettings
from modules.utils import get_current_time
from services.ai_orchestrator import interrupt_active_ai_runs

logger = logging.getLogger(__name__)


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
        while True:
            await asyncio.sleep(24 * 60 * 60)
            try:
                await self.cleanup_once()
            except Exception:
                logger.exception("AI conversation retention cleanup failed")

    async def cleanup_once(self) -> int:
        async with async_session_maker() as db:
            settings = await AISystemSettings.get_or_create(db)
            cutoff = get_current_time() - timedelta(days=settings.history_retention_days)
            result = await db.execute(
                select(AIConversation).where(AIConversation.updated_at < cutoff)
            )
            conversations = list(result.scalars().all())
            for conversation in conversations:
                await db.delete(conversation)
            await db.commit()
            return len(conversations)


ai_retention_service = AIRetentionService()
