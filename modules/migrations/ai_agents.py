"""Create secure AI-agent diagnostic and managed-install metadata."""

# ruff: noqa: F403,F405

from .common import *


async def _add_column(conn: AsyncConnection, table: str, column: str, definition: str) -> None:
    if await table_exists(conn, table) and not await column_exists(conn, table, column):
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


async def migrate_ai_agents(conn: AsyncConnection) -> None:
    """Create new tables and extend existing rows without destructive rewrites."""
    await conn.run_sync(SQLModel.metadata.create_all)
    await _add_column(conn, "ai_tool_runs", "approval_expires_at", "DATETIME NULL")
    await _add_column(
        conn,
        "managed_plugins",
        "install_recipe_id",
        "INT NULL",
    )
    await _add_column(
        conn,
        "managed_plugins",
        "installed_asset_name",
        "VARCHAR(500) NULL",
    )
    await _add_column(conn, "managed_plugins", "archive_sha256", "VARCHAR(64) NULL")
    await _add_column(
        conn,
        "managed_plugins",
        "config_policy",
        "VARCHAR(32) NOT NULL DEFAULT 'preserve'",
    )
