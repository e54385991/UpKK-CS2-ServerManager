"""Daily retention for administrator audit rows and Discord operation details."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from sqlmodel import col, delete, select

from modules.database import async_session_maker
from modules.models import AuditLog, DiscordOperationRun
from modules.utils import get_current_time
from services.audit_log_service import (
    configured_retention_days,
    record_discord_operation_event,
    retention_cutoff,
)

logger = logging.getLogger(__name__)
AUDIT_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60


def _naive_now():
    moment = get_current_time()
    return moment if moment.tzinfo is None else moment.replace(tzinfo=None)


class AuditRetentionService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
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
            await asyncio.sleep(AUDIT_CLEANUP_INTERVAL_SECONDS)
            try:
                await self.cleanup_once()
            except Exception:
                logger.exception("Audit retention cleanup failed")

    async def expire_pending_discord_operations(self) -> int:
        now = _naive_now()
        async with async_session_maker() as db:
            result = await db.execute(
                select(DiscordOperationRun).where(DiscordOperationRun.status == "pending")
            )
            expired: list[DiscordOperationRun] = []
            for item in result.scalars().all():
                expires_at = item.expires_at
                if expires_at is None:
                    continue
                if expires_at.tzinfo is not None:
                    expires_at = expires_at.replace(tzinfo=None)
                if expires_at <= now:
                    item.status = "expired"
                    db.add(item)
                    expired.append(item)
            await db.commit()
        for item in expired:
            await record_discord_operation_event(item, "expired")
        return len(expired)

    async def delete_expired_rows(self) -> tuple[int, int, int]:
        async with async_session_maker() as db:
            days = await configured_retention_days(db)
            cutoff = retention_cutoff(days=days)
            audit_result = await db.execute(
                delete(AuditLog).where(col(AuditLog.created_at) < cutoff)
            )
            operation_result = await db.execute(
                delete(DiscordOperationRun).where(col(DiscordOperationRun.created_at) < cutoff)
            )
            await db.commit()
        return (
            int(getattr(audit_result, "rowcount", 0) or 0),
            int(getattr(operation_result, "rowcount", 0) or 0),
            days,
        )

    async def cleanup_once(self) -> dict[str, int]:
        expired = await self.expire_pending_discord_operations()
        deleted_audit, deleted_operations, days = await self.delete_expired_rows()
        if expired or deleted_audit or deleted_operations:
            logger.info(
                "Audit retention: expired=%s deleted_audit=%s deleted_discord_ops=%s days=%s",
                expired,
                deleted_audit,
                deleted_operations,
                days,
            )
        return {
            "expired_operations": expired,
            "deleted_audit_logs": deleted_audit,
            "deleted_discord_operations": deleted_operations,
            "retention_days": days,
        }


audit_retention_service = AuditRetentionService()
