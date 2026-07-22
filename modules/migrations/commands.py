"""Migrate custom commands and fractional update intervals."""

# ruff: noqa: F403,F405

from .common import *


async def migrate_commands(conn: AsyncConnection) -> None:
    """Migrate custom commands and fractional update intervals."""
    result = await conn.execute(
        text("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'custom_commands'
        """)
    )

    custom_commands_exists = result.fetchone() is not None

    if not custom_commands_exists:
        result = await conn.execute(
            text("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME IN ('users', 'servers')
            """)
        )
        required_tables_count = result.scalar() or 0

        if required_tables_count >= 2:
            print("Creating custom_commands table...")
            await conn.execute(
                text("""
                    CREATE TABLE custom_commands (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NOT NULL,
                        server_id INT NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        target VARCHAR(30) NOT NULL DEFAULT 'host',
                        commands TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_custom_commands_user_server (user_id, server_id),
                        INDEX idx_custom_commands_server (server_id),
                        CONSTRAINT fk_custom_commands_user_id
                            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                        CONSTRAINT fk_custom_commands_server_id
                            FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
                    )
                """)
            )
            print("Migration completed: custom_commands table created")
        else:
            print("custom_commands table will be created during database initialization")
    else:
        print("custom_commands table exists")

    result = await conn.execute(
        text("""
            SELECT DATA_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'servers' 
            AND COLUMN_NAME = 'update_check_interval_hours'
        """)
    )

    column_type = result.fetchone()

    if column_type and column_type[0].upper() in (
        "INT",
        "TINYINT",
        "SMALLINT",
        "MEDIUMINT",
        "BIGINT",
    ):
        print("Migrating update_check_interval_hours from INT to FLOAT...")
        await conn.execute(
            text("""
                ALTER TABLE servers 
                MODIFY COLUMN update_check_interval_hours FLOAT NOT NULL DEFAULT 1.0
            """)
        )
        print(
            "✓ Migration completed: update_check_interval_hours changed to FLOAT for fractional hour support"
        )
    else:
        print("✓ update_check_interval_hours column type is already FLOAT or does not exist")
