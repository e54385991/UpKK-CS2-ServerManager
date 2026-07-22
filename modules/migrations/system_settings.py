"""Migrate system-wide GitHub and proxy defaults."""

# ruff: noqa: F403,F405

from .common import *


async def migrate_system_settings(conn: AsyncConnection) -> None:
    """Migrate system-wide GitHub and proxy defaults."""
    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'system_settings'
            AND COLUMN_NAME = 'global_github_token'
        """)
    )

    if result.fetchone() is None:
        await conn.execute(
            text("""
                ALTER TABLE system_settings
                ADD COLUMN global_github_token VARCHAR(255) NULL
                AFTER github_proxy_url
            """)
        )
        print("Migration completed: global_github_token added to system_settings")
    else:
        print("global_github_token column exists in system_settings")

    result = await conn.execute(
        text("""
            SELECT COLUMN_DEFAULT
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'system_settings'
            AND COLUMN_NAME = 'default_proxy_mode'
        """)
    )

    proxy_mode_column = result.fetchone()

    if proxy_mode_column and proxy_mode_column[0] != "panel":
        await conn.execute(
            text("""
                ALTER TABLE system_settings
                MODIFY COLUMN default_proxy_mode VARCHAR(50) NOT NULL DEFAULT 'panel'
            """)
        )
        print("Migration completed: new system settings default to panel proxy")
