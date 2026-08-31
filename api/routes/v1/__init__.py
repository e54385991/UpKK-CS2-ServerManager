"""Versioned ``/api/v1`` surface for the Next.js console.

This package is the forward-looking, browser-facing HTTP contract. It returns
non-secret projections (see :mod:`api.routes.v1.schemas`) and reuses the shared
authorization dependencies so ownership and the legacy 404 policy are preserved.
Legacy ``/api/*`` routes remain untouched for existing clients.
"""

from fastapi import APIRouter

from . import (
    assistant,
    audit,
    auth,
    batch,
    cleanup,
    console,
    custom_commands,
    discord,
    discord_servers,
    files,
    game_modes,
    game_updates,
    github_plugins,
    maps,
    operation_inbox,
    operations,
    overview,
    plugin_catalog,
    plugin_configs,
    plugin_diagnostics,
    plugin_updates,
    plugins,
    profile,
    s3_backups,
    schedule,
    server_configs,
    servers,
    settings,
    setup,
    ssh_pool,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(profile.router)
router.include_router(batch.router)
router.include_router(servers.router)
router.include_router(github_plugins.market_router)
router.include_router(github_plugins.server_router)
router.include_router(custom_commands.router)
router.include_router(cleanup.router)
router.include_router(s3_backups.router)
router.include_router(server_configs.router)
router.include_router(operation_inbox.router)
router.include_router(operations.router)
router.include_router(plugins.market_router)
router.include_router(plugins.server_router)
router.include_router(plugin_catalog.router)
router.include_router(plugin_configs.router)
router.include_router(overview.router)
router.include_router(audit.router)
router.include_router(settings.router)
router.include_router(maps.router)
router.include_router(game_modes.router)
router.include_router(files.router)
router.include_router(console.router)
router.include_router(assistant.router)
router.include_router(discord.router)
router.include_router(discord_servers.router)
router.include_router(schedule.router)
router.include_router(plugin_updates.router)
router.include_router(plugin_diagnostics.router)
router.include_router(setup.router)
router.include_router(game_updates.router)
router.include_router(ssh_pool.router)

__all__ = ["router"]
