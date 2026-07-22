"""Migrate legacy server identity, API, A2S, CPU and session fields."""

# ruff: noqa: F403,F405

from .common import *


async def migrate_server_core(conn: AsyncConnection) -> None:
    """Migrate legacy server identity, API, A2S, CPU and session fields."""
    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'servers' 
            AND COLUMN_NAME = 'sudo_password'
        """)
    )

    column_exists = result.fetchone() is not None

    if not column_exists:
        print("Adding sudo_password column to servers table...")
        await conn.execute(
            text("""
                ALTER TABLE servers 
                ADD COLUMN sudo_password VARCHAR(255) NULL 
                AFTER ssh_key_path
            """)
        )
        print("✓ Migration completed: sudo_password column added")
    else:
        print("✓ sudo_password column exists")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'servers' 
            AND COLUMN_NAME = 'user_id'
        """)
    )

    user_id_exists = result.fetchone() is not None

    if not user_id_exists:
        print("Adding user_id column to servers table...")
        # First check if users table exists
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

        # Add user_id column with a default user (will be updated later)
        await conn.execute(
            text("""
                ALTER TABLE servers 
                ADD COLUMN user_id INT NOT NULL DEFAULT 1 
                AFTER id,
                ADD INDEX idx_servers_user_id (user_id)
            """)
        )
        print("✓ Migration completed: user_id column added")

        # Remove unique constraint from server name
        try:
            await conn.execute(
                text("""
                    ALTER TABLE servers 
                    DROP INDEX name
                """)
            )
            print("✓ Removed unique constraint from server name")
        except Exception as e:
            print(f"Note: Could not remove unique constraint (might not exist): {e}")
    else:
        print("✓ user_id column exists")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'servers' 
            AND COLUMN_NAME = 'api_key'
        """)
    )

    api_key_exists = result.fetchone() is not None

    if not api_key_exists:
        print("Adding api_key column to servers table...")
        await conn.execute(
            text("""
                ALTER TABLE servers 
                ADD COLUMN api_key VARCHAR(64) NULL
            """)
        )
        # Add unique index
        try:
            await conn.execute(
                text("""
                    CREATE UNIQUE INDEX idx_server_api_key ON servers(api_key)
                """)
            )
            print("✓ Migration completed: api_key column and index added")
        except Exception as e:
            print(f"✓ Migration completed: api_key column added (index may already exist): {e}")
    else:
        print("✓ api_key column exists")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'servers' 
            AND COLUMN_NAME = 'backend_url'
        """)
    )

    backend_url_exists = result.fetchone() is not None

    if not backend_url_exists:
        print("Adding backend_url column to servers table...")
        await conn.execute(
            text("""
                ALTER TABLE servers 
                ADD COLUMN backend_url VARCHAR(500) NULL
            """)
        )
        print("✓ Migration completed: backend_url column added")
    else:
        print("✓ backend_url column exists")

    a2s_columns = [
        "a2s_query_host",
        "a2s_query_port",
        "enable_a2s_monitoring",
        "a2s_failure_threshold",
        "a2s_check_interval_seconds",
    ]

    for column in a2s_columns:
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
            if column == "a2s_query_host":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN a2s_query_host VARCHAR(255) NULL
                    """)
                )
            elif column == "a2s_query_port":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN a2s_query_port INT NULL
                    """)
                )
            elif column == "enable_a2s_monitoring":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN enable_a2s_monitoring TINYINT(1) DEFAULT 0
                    """)
                )
            elif column == "a2s_failure_threshold":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN a2s_failure_threshold INT DEFAULT 3
                    """)
                )
            elif column == "a2s_check_interval_seconds":
                await conn.execute(
                    text("""
                        ALTER TABLE servers 
                        ADD COLUMN a2s_check_interval_seconds INT DEFAULT 60
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
            AND COLUMN_NAME = 'cpu_affinity'
        """)
    )

    cpu_affinity_exists = result.fetchone() is not None

    if not cpu_affinity_exists:
        print("Adding cpu_affinity column to servers table...")
        await conn.execute(
            text("""
                ALTER TABLE servers 
                ADD COLUMN cpu_affinity VARCHAR(500) NULL
            """)
        )
        print("✓ Migration completed: cpu_affinity column added")
    else:
        print("✓ cpu_affinity column exists")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME, COLUMN_DEFAULT
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'servers'
            AND COLUMN_NAME = 'session_manager'
        """)
    )

    session_manager_column = result.fetchone()

    session_manager_exists = session_manager_column is not None

    session_manager_default = (
        session_manager_column[1] if session_manager_column is not None else None
    )

    if not session_manager_exists:
        print("Adding session_manager column to servers table...")
        await conn.execute(
            text("""
                ALTER TABLE servers
                ADD COLUMN session_manager VARCHAR(16) NOT NULL DEFAULT 'screen'
            """)
        )
        print("✓ Migration completed: session_manager column added")
        session_manager_default = "screen"
    else:
        print("✓ session_manager column exists")

    if session_manager_default != "tmux":
        await conn.execute(
            text("""
                ALTER TABLE servers
                MODIFY COLUMN session_manager VARCHAR(16) NOT NULL DEFAULT 'tmux'
            """)
        )
        print("✓ session_manager default set to tmux (existing values preserved)")
    else:
        print("✓ session_manager default is tmux")
