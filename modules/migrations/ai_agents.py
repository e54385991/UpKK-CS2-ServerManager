"""Create secure AI-agent diagnostic and managed-install metadata."""

# ruff: noqa: F403,F405

from .common import *


async def _add_column(conn: AsyncConnection, table: str, column: str, definition: str) -> None:
    if await table_exists(conn, table) and not await column_exists(conn, table, column):
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


async def _index_columns(conn: AsyncConnection, table: str, index: str) -> tuple[str, ...]:
    """Return the ordered MySQL columns for an index, or an empty tuple."""
    result = await conn.execute(
        text(
            """
            SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',')
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND INDEX_NAME = :index
            """
        ),
        {"table": table, "index": index},
    )
    row = result.fetchone()
    return tuple(row[0].split(",")) if row and row[0] else ()


async def _migrate_managed_plugin_file_identity(conn: AsyncConnection) -> None:
    """Replace the utf8mb4 path index with a fixed-size path digest."""
    table = "managed_plugin_files"
    index = "uq_managed_plugin_file"
    if not await table_exists(conn, table):
        return

    await _add_column(conn, table, "path_hash", "VARCHAR(64) NULL")
    await conn.execute(
        text(
            """
            UPDATE managed_plugin_files
            SET path_hash = LOWER(SHA2(relative_path, 256))
            WHERE path_hash IS NULL OR path_hash = ''
            """
        )
    )
    await conn.execute(
        text("ALTER TABLE managed_plugin_files MODIFY COLUMN path_hash VARCHAR(64) NOT NULL")
    )

    columns = await _index_columns(conn, table, index)
    expected = ("managed_plugin_id", "path_hash")
    if columns and columns != expected:
        await conn.execute(text(f"ALTER TABLE {table} DROP INDEX {index}"))
        columns = ()
    if not columns:
        await conn.execute(
            text(f"CREATE UNIQUE INDEX {index} ON {table} (managed_plugin_id, path_hash)")
        )


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
    await _migrate_managed_plugin_file_identity(conn)
