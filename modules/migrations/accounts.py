"""Migrate sudo, OAuth and S3 account configuration."""

# ruff: noqa: F403,F405

from .common import *


async def migrate_accounts(conn: AsyncConnection) -> None:
    """Migrate sudo, OAuth and S3 account configuration."""
    result = await conn.execute(
        text("""
            SELECT TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'ssh_servers_sudo'
        """)
    )

    ssh_servers_sudo_exists = result.fetchone() is not None

    if not ssh_servers_sudo_exists:
        print("Creating ssh_servers_sudo table...")
        await conn.execute(
            text("""
                CREATE TABLE ssh_servers_sudo (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    host VARCHAR(255) NOT NULL,
                    ssh_port INT NOT NULL DEFAULT 22,
                    sudo_user VARCHAR(100) NOT NULL,
                    sudo_password VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_ssh_sudo_config (user_id, host, ssh_port, sudo_user),
                    INDEX idx_ssh_servers_sudo_user_id (user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
        )
        print("✓ Migration completed: ssh_servers_sudo table created")
    else:
        print("✓ ssh_servers_sudo table exists")

    google_columns = ["google_id", "oauth_provider"]

    for column in google_columns:
        result = await conn.execute(
            text(f"""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'users' 
                AND COLUMN_NAME = '{column}'
            """)
        )
        column_exists = result.fetchone() is not None

        if not column_exists:
            print(f"Adding {column} column to users table...")
            if column == "google_id":
                await conn.execute(
                    text("""
                        ALTER TABLE users 
                        ADD COLUMN google_id VARCHAR(255) NULL
                    """)
                )
                # Add unique index for google_id
                try:
                    await conn.execute(
                        text("""
                            CREATE UNIQUE INDEX idx_user_google_id ON users(google_id)
                        """)
                    )
                    print(f"✓ Migration completed: {column} column and index added to users table")
                except Exception as index_error:
                    # Index may already exist, which is fine
                    print(
                        f"✓ Migration completed: {column} column added to users table (index may already exist): {index_error}"
                    )
            elif column == "oauth_provider":
                await conn.execute(
                    text("""
                        ALTER TABLE users 
                        ADD COLUMN oauth_provider VARCHAR(50) NULL
                    """)
                )
                print(f"✓ Migration completed: {column} column added to users table")
        else:
            print(f"✓ {column} column exists in users table")

    s3_columns = {
        "s3_enabled": "TINYINT(1) DEFAULT 0",
        "s3_endpoint_url": "VARCHAR(500) NULL",
        "s3_region": "VARCHAR(100) NULL",
        "s3_bucket": "VARCHAR(255) NULL",
        "s3_access_key_id": "VARCHAR(255) NULL",
        "s3_secret_access_key": "VARCHAR(255) NULL",
        "s3_prefix": "VARCHAR(255) NULL",
        "s3_use_ssl": "TINYINT(1) DEFAULT 1",
        "s3_retention_count": "INT NULL",
    }

    for column, definition in s3_columns.items():
        result = await conn.execute(
            text(f"""
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'users'
                AND COLUMN_NAME = '{column}'
            """)
        )
        column_exists = result.fetchone() is not None

        if not column_exists:
            print(f"Adding {column} column to users table...")
            await conn.execute(
                text(f"""
                    ALTER TABLE users
                    ADD COLUMN {column} {definition}
                """)
            )
            print(f"Migration completed: {column} column added to users table")
        else:
            print(f"{column} column exists in users table")
