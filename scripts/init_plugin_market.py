"""Seed the plugin marketplace from the shipped default catalog when empty."""

from __future__ import annotations

import asyncio

from modules.database import async_session_maker
from services.plugin_catalog import ensure_default_plugin_catalog


async def populate_plugin_market() -> None:
    async with async_session_maker() as session:
        summary = await ensure_default_plugin_catalog(session)
    if summary is None:
        print("Plugin market already has plugins. Skipping default catalog import.")
        return
    print(
        "Default plugin catalog imported: "
        f"{summary.imported} imported, {summary.failed} failed, {summary.total} total."
    )


if __name__ == "__main__":
    asyncio.run(populate_plugin_market())
