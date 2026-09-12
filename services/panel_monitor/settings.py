"""Read and apply the persisted panel monitoring switch."""

from __future__ import annotations

import asyncio
import logging

from modules.database import async_session_maker
from modules.models.system import SystemSettings

logger = logging.getLogger(__name__)
READ_TIMEOUT_SECONDS = 2.0


async def read_monitoring_enabled() -> bool | None:
    """Return the stored flag, or None when the row cannot be read."""
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            async with async_session_maker() as session:
                row = await SystemSettings.get_settings(session)
                await session.commit()
        if row is None:
            return True
        return bool(getattr(row, "panel_monitoring_enabled", True))
    except Exception:
        logger.debug("Could not read panel_monitoring_enabled", exc_info=True)
        return None
