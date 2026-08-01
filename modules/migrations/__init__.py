"""Ordered, idempotent legacy database migrations."""

from sqlalchemy.ext.asyncio import AsyncConnection

from .accounts import migrate_accounts
from .ai import migrate_ai
from .ai_agents import migrate_ai_agents
from .bootstrap import migrate_bootstrap
from .commands import migrate_commands
from .common import MigrationStep, column_exists, table_exists
from .plugins import migrate_plugins
from .server_core import migrate_server_core
from .server_features import migrate_server_features
from .system_settings import migrate_system_settings

MIGRATION_STEPS = (
    MigrationStep("bootstrap", migrate_bootstrap),
    MigrationStep("server_core", migrate_server_core),
    MigrationStep("server_features", migrate_server_features),
    MigrationStep("plugins", migrate_plugins),
    MigrationStep("accounts", migrate_accounts),
    MigrationStep("commands", migrate_commands),
    MigrationStep("system_settings", migrate_system_settings),
    MigrationStep("ai", migrate_ai),
    MigrationStep("ai_agents", migrate_ai_agents),
)


async def run_migrations(conn: AsyncConnection) -> None:
    for step in MIGRATION_STEPS:
        await step.migrate(conn)
    print("✓ Database schema migration completed")


__all__ = [
    "MIGRATION_STEPS",
    "MigrationStep",
    "column_exists",
    "run_migrations",
    "table_exists",
]
