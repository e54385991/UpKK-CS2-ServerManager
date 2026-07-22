"""Migrate account keys and server monitoring/integration settings."""

# ruff: noqa: F403,F405

from .common import *


async def migrate_server_features(conn: AsyncConnection) -> None:
    """Migrate account keys and server monitoring/integration settings."""
    result = await conn.execute(
        text("""
            SELECT TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'users'
        """)
    )

    users_table_exists = result.fetchone() is not None

    if not users_table_exists:
        print("Creating users table first...")
        await conn.run_sync(SQLModel.metadata.create_all)

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'users' 
            AND COLUMN_NAME = 'api_key'
        """)
    )

    user_api_key_exists = result.fetchone() is not None

    if not user_api_key_exists:
        print("Adding api_key column to users table...")
        await conn.execute(
            text("""
                ALTER TABLE users 
                ADD COLUMN api_key VARCHAR(64) NULL
            """)
        )
        # Add unique index
        try:
            await conn.execute(
                text("""
                    CREATE UNIQUE INDEX idx_user_api_key ON users(api_key)
                """)
            )
            print("✓ Migration completed: api_key column and index added to users table")
        except Exception as e:
            print(
                f"✓ Migration completed: api_key column added to users table (index may already exist): {e}"
            )
    else:
        print("✓ api_key column exists in users table")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'users' 
            AND COLUMN_NAME = 'steam_api_key'
        """)
    )

    steam_api_key_exists = result.fetchone() is not None

    if not steam_api_key_exists:
        print("Adding steam_api_key column to users table...")
        await conn.execute(
            text("""
                ALTER TABLE users 
                ADD COLUMN steam_api_key VARCHAR(64) NULL
            """)
        )
        print("✓ Migration completed: steam_api_key column added to users table")
    else:
        print("✓ steam_api_key column exists in users table")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'servers' 
            AND COLUMN_NAME = 'steam_account_token'
        """)
    )

    steam_account_token_exists = result.fetchone() is not None

    if not steam_account_token_exists:
        print("Adding steam_account_token column to servers table...")
        await conn.execute(
            text("""
                ALTER TABLE servers 
                ADD COLUMN steam_account_token VARCHAR(255) NULL
            """)
        )
        print("✓ Migration completed: steam_account_token column added to servers table")
    else:
        print("✓ steam_account_token column exists in servers table")

    ssh_tracking_columns = [
        "last_ssh_success",
        "last_ssh_failure",
        "consecutive_ssh_failures",
        "is_ssh_down",
    ]

    for column in ssh_tracking_columns:
        result = await conn.execute(
            text(f"""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'servers' 
                AND COLUMN_NAME = '{column}'
            """)
        )
        column_exists = result.fetchone() is not None

        if not column_exists:
            print(f"Adding {column} column to servers table...")
            if column == "last_ssh_success":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN last_ssh_success TIMESTAMP NULL
                    """)
                )
            elif column == "last_ssh_failure":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN last_ssh_failure TIMESTAMP NULL
                    """)
                )
            elif column == "consecutive_ssh_failures":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN consecutive_ssh_failures INT DEFAULT 0
                    """)
                )
            elif column == "is_ssh_down":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN is_ssh_down TINYINT(1) DEFAULT 0
                    """)
                )
            print(f"✓ Migration completed: {column} column added")
        else:
            print(f"✓ {column} column exists")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'servers' 
            AND COLUMN_NAME = 'github_proxy'
        """)
    )

    github_proxy_exists = result.fetchone() is not None

    if not github_proxy_exists:
        print("Adding github_proxy column to servers table...")
        await conn.execute(
            text("""
                ALTER TABLE servers 
                ADD COLUMN github_proxy VARCHAR(500) NULL
            """)
        )
        print("✓ Migration completed: github_proxy column added")
    else:
        print("✓ github_proxy column exists")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'servers' 
            AND COLUMN_NAME = 'use_panel_proxy'
        """)
    )

    use_panel_proxy_exists = result.fetchone() is not None

    if not use_panel_proxy_exists:
        print("Adding use_panel_proxy column to servers table...")
        await conn.execute(
            text("""
                ALTER TABLE servers 
                ADD COLUMN use_panel_proxy TINYINT(1) DEFAULT 0
            """)
        )
        print("✓ Migration completed: use_panel_proxy column added")
    else:
        print("✓ use_panel_proxy column exists")

    discord_columns = {
        "discord_notifications_enabled": "TINYINT(1) DEFAULT 0",
        "discord_webhook_url": "VARCHAR(1000) NULL",
        "discord_channel_name": "VARCHAR(255) NULL",
        "discord_notify_auto_updates": "TINYINT(1) DEFAULT 1",
        "discord_notify_manual_updates": "TINYINT(1) DEFAULT 1",
        "discord_notify_plugin_updates": "TINYINT(1) DEFAULT 1",
        "discord_notify_s3_backups": "TINYINT(1) DEFAULT 1",
        "discord_notify_crash_restarts": "TINYINT(1) DEFAULT 1",
        "discord_crash_restart_min_interval_minutes": "INT DEFAULT 10",
    }

    for column, definition in discord_columns.items():
        result = await conn.execute(
            text(f"""
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'servers'
                AND COLUMN_NAME = '{column}'
            """)
        )
        column_exists = result.fetchone() is not None

        if not column_exists:
            print(f"Adding {column} column to servers table...")
            await conn.execute(
                text(f"""
                    ALTER TABLE servers
                    ADD COLUMN {column} {definition}
                """)
            )
            print(f"Migration completed: {column} column added to servers table")
        else:
            print(f"{column} column exists in servers table")

    plugin_update_columns = {
        "enable_plugin_auto_update": "TINYINT(1) NOT NULL DEFAULT 0",
        "plugin_update_check_interval_hours": "FLOAT NOT NULL DEFAULT 1.0",
        "last_plugin_update_check": "DATETIME NULL",
    }

    for column, definition in plugin_update_columns.items():
        result = await conn.execute(
            text(f"""
                SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'servers'
                AND COLUMN_NAME = '{column}'
            """)
        )
        if result.fetchone() is None:
            await conn.execute(text(f"ALTER TABLE servers ADD COLUMN {column} {definition}"))
            print(f"Migration completed: {column} column added to servers table")

    ssh_health_columns = [
        "enable_ssh_health_monitoring",
        "ssh_health_check_interval_hours",
        "ssh_health_failure_threshold",
        "last_ssh_health_check",
        "ssh_health_status",
    ]

    for column in ssh_health_columns:
        result = await conn.execute(
            text(f"""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'servers' 
                AND COLUMN_NAME = '{column}'
            """)
        )
        column_exists = result.fetchone() is not None

        if not column_exists:
            print(f"Adding {column} column to servers table...")
            if column == "enable_ssh_health_monitoring":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN enable_ssh_health_monitoring TINYINT(1) DEFAULT 1
                    """)
                )
            elif column == "ssh_health_check_interval_hours":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN ssh_health_check_interval_hours INT DEFAULT 2
                    """)
                )
            elif column == "ssh_health_failure_threshold":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN ssh_health_failure_threshold INT DEFAULT 84
                    """)
                )
            elif column == "last_ssh_health_check":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN last_ssh_health_check TIMESTAMP NULL
                    """)
                )
            elif column == "ssh_health_status":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN ssh_health_status VARCHAR(50) DEFAULT 'unknown'
                    """)
                )
            print(f"✓ Migration completed: {column} column added")
        else:
            print(f"✓ {column} column exists")
