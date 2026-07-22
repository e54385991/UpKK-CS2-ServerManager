"""Migrate plugin market, managed plugins and configuration sources."""

# ruff: noqa: F403,F405

from .common import *


async def migrate_plugins(conn: AsyncConnection) -> None:
    """Migrate plugin market, managed plugins and configuration sources."""
    result = await conn.execute(
        text("""
            SELECT TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'market_plugins'
        """)
    )

    market_plugins_exists = result.fetchone() is not None

    if not market_plugins_exists:
        print("Creating market_plugins table...")
        await conn.execute(
            text("""
                CREATE TABLE market_plugins (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    github_url VARCHAR(500) NOT NULL UNIQUE,
                    title VARCHAR(255) NOT NULL,
                    description TEXT NULL,
                    author VARCHAR(255) NULL,
                    version VARCHAR(50) NULL,
                    category ENUM('GAME_MODE', 'ENTERTAINMENT', 'UTILITY', 'ADMIN', 'PERFORMANCE', 'LIBRARY', 'OTHER') NOT NULL DEFAULT 'OTHER',
                    tags TEXT NULL,
                    is_recommended TINYINT(1) DEFAULT 0,
                    icon_url VARCHAR(500) NULL,
                    download_count INT DEFAULT 0,
                    install_count INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_market_plugins_github_url (github_url),
                    INDEX idx_market_plugins_title (title)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
        )
        print("✓ Migration completed: market_plugins table created")
    else:
        print("✓ market_plugins table exists")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'market_plugins' 
            AND COLUMN_NAME = 'dependencies'
        """)
    )

    dependencies_exists = result.fetchone() is not None

    if not dependencies_exists:
        print("Adding dependencies column to market_plugins table...")
        await conn.execute(
            text("""
                ALTER TABLE market_plugins 
                ADD COLUMN dependencies TEXT NULL
            """)
        )
        print("✓ Migration completed: dependencies column added to market_plugins")
    else:
        print("✓ dependencies column exists in market_plugins table")

    result = await conn.execute(
        text("""
            SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'managed_plugins'
        """)
    )

    if result.fetchone() is None:
        await conn.execute(
            text("""
                CREATE TABLE managed_plugins (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    server_id INT NOT NULL,
                    source_type VARCHAR(30) NOT NULL,
                    source_key VARCHAR(500) NOT NULL,
                    display_name VARCHAR(255) NOT NULL,
                    repo_url VARCHAR(500) NULL,
                    market_plugin_id INT NULL,
                    framework_key VARCHAR(100) NULL,
                    installed_release_id VARCHAR(100) NULL,
                    installed_version VARCHAR(100) NOT NULL DEFAULT 'unknown',
                    latest_version VARCHAR(100) NULL,
                    asset_glob VARCHAR(500) NULL,
                    custom_install_path VARCHAR(255) NULL,
                    exclude_dirs JSON NOT NULL,
                    exclude_files JSON NOT NULL,
                    auto_update_enabled TINYINT(1) NOT NULL DEFAULT 0,
                    backup_before_update TINYINT(1) NOT NULL DEFAULT 0,
                    restart_after_update TINYINT(1) NOT NULL DEFAULT 0,
                    last_check_at DATETIME NULL,
                    last_update_at DATETIME NULL,
                    last_status VARCHAR(30) NULL,
                    last_error TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_managed_plugins_server_id (server_id),
                    UNIQUE KEY uq_managed_plugin_source (server_id, source_type, source_key),
                    CONSTRAINT fk_managed_plugins_server FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE,
                    CONSTRAINT fk_managed_plugins_market FOREIGN KEY (market_plugin_id) REFERENCES market_plugins(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
        )
        print("Migration completed: managed_plugins table created")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'managed_plugins'
            AND COLUMN_NAME = 'restart_after_update'
        """)
    )

    if result.fetchone() is None:
        await conn.execute(
            text(
                "ALTER TABLE managed_plugins ADD COLUMN restart_after_update TINYINT(1) NOT NULL DEFAULT 0"
            )
        )
        print("Migration completed: restart_after_update added to managed_plugins")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'managed_plugins'
            AND COLUMN_NAME = 'backup_before_update'
        """)
    )

    if result.fetchone() is None:
        await conn.execute(
            text(
                "ALTER TABLE managed_plugins ADD COLUMN backup_before_update TINYINT(1) NOT NULL DEFAULT 0"
            )
        )
        print("Migration completed: backup_before_update added to managed_plugins")

    result = await conn.execute(
        text("""
            SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'plugin_config_sources'
        """)
    )

    if result.fetchone() is None:
        await conn.execute(
            text("""
                CREATE TABLE plugin_config_sources (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    server_id INT NOT NULL,
                    relative_path VARCHAR(1000) NOT NULL,
                    path_hash VARCHAR(64) NOT NULL,
                    source_type VARCHAR(16) NOT NULL,
                    is_default TINYINT(1) NOT NULL DEFAULT 0,
                    is_enabled TINYINT(1) NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_plugin_config_sources_server_id (server_id),
                    UNIQUE KEY uq_plugin_config_source_path (server_id, path_hash),
                    CONSTRAINT fk_plugin_config_sources_server
                        FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
        )
        print("Migration completed: plugin_config_sources table created")

    result = await conn.execute(
        text("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'plugin_config_sources'
            AND COLUMN_NAME = 'is_enabled'
        """)
    )

    if result.fetchone() is None:
        await conn.execute(
            text(
                "ALTER TABLE plugin_config_sources ADD COLUMN is_enabled TINYINT(1) NOT NULL DEFAULT 1"
            )
        )

    await conn.execute(
        text("""
            INSERT INTO plugin_config_sources
                (server_id, relative_path, path_hash, source_type, is_default, is_enabled)
            SELECT
                servers.id,
                'cs2/game/csgo/addons/counterstrikesharp/configs/Advertisement',
                '8a2ee85b3e0335ec0d294b4a6e110dc46a956a4d2fc045c33265a71812165e49',
                'directory',
                1,
                1
            FROM servers
            LEFT JOIN plugin_config_sources sources
                ON sources.server_id = servers.id
                AND sources.path_hash = '8a2ee85b3e0335ec0d294b4a6e110dc46a956a4d2fc045c33265a71812165e49'
            WHERE sources.id IS NULL
        """)
    )

    result = await conn.execute(
        text("""
            SELECT COLUMN_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'market_plugins' 
            AND COLUMN_NAME = 'category'
        """)
    )

    category_type = result.fetchone()

    if category_type and "game_mode" in category_type[0]:
        print("Migrating category enum from lowercase to uppercase...")
        # SQLAlchemy expects uppercase enum names, so we need to update the database
        try:
            await conn.execute(
                text("""
                    ALTER TABLE market_plugins 
                    MODIFY COLUMN category ENUM('GAME_MODE', 'ENTERTAINMENT', 'UTILITY', 'ADMIN', 'PERFORMANCE', 'LIBRARY', 'OTHER') NOT NULL DEFAULT 'OTHER'
                """)
            )
            print("✓ Migration completed: category enum values updated to uppercase")
        except Exception as e:
            print(f"Note: Could not update category enum (might already be updated): {e}")
    else:
        print("✓ category enum is using correct uppercase values")
