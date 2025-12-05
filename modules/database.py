"""
Database connection and session management (Async)
Using SQLModel for seamless FastAPI integration
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from sqlmodel import SQLModel
from .config import settings
from typing import AsyncGenerator

# Create async database engine with connection pool configuration
# Using settings from config.py for optimal performance
engine = create_async_engine(
    settings.mysql_url,
    pool_size=settings.MYSQL_POOL_SIZE,  # Number of connections to keep open
    max_overflow=settings.MYSQL_MAX_OVERFLOW,  # Max overflow connections
    pool_timeout=settings.MYSQL_POOL_TIMEOUT,  # Wait time for connection
    pool_recycle=settings.MYSQL_POOL_RECYCLE,  # Connection recycle time
    pool_pre_ping=settings.MYSQL_POOL_PRE_PING,  # Health check before use
    echo=settings.MYSQL_ECHO  # Enable/disable SQL query logging
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Alias for backward compatibility
async_session_maker = AsyncSessionLocal


async def init_db():
    """Initialize database tables using SQLModel"""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    print("Database initialized successfully!")
    
    # Create default admin user if no users exist
    from sqlmodel import select
    from .models import User
    from .auth import get_password_hash
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        
        if not users:
            print("Creating default admin user...")
            admin_user = User(
                username="admin",
                email="admin@example.com",
                hashed_password=get_password_hash("admin123"),
                is_admin=True,
                is_active=True
            )
            session.add(admin_user)
            await session.commit()
            print("✓ Default admin user created:")
            print("  Username: admin")
            print("  Password: admin123")
            print("  ⚠️  IMPORTANT: Please change the default password after first login!")


async def migrate_db():
    """Run database migrations for schema updates"""
    async with engine.begin() as conn:
        # Check if sudo_password column exists, add it if not
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
        
        # Check if user_id column exists in servers table
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
        
        # Check if api_key column exists in servers table
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
        
        # Check if backend_url column exists in servers table
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
        
        # Check if A2S monitoring columns exist in servers table
        a2s_columns = ['a2s_query_host', 'a2s_query_port', 'enable_a2s_monitoring', 'a2s_failure_threshold', 'a2s_check_interval_seconds']
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
                if column == 'a2s_query_host':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN a2s_query_host VARCHAR(255) NULL
                        """)
                    )
                elif column == 'a2s_query_port':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN a2s_query_port INT NULL
                        """)
                    )
                elif column == 'enable_a2s_monitoring':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN enable_a2s_monitoring TINYINT(1) DEFAULT 0
                        """)
                    )
                elif column == 'a2s_failure_threshold':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN a2s_failure_threshold INT DEFAULT 3
                        """)
                    )
                elif column == 'a2s_check_interval_seconds':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN a2s_check_interval_seconds INT DEFAULT 60
                        """)
                    )
                print(f"✓ Migration completed: {column} column added")
            else:
                print(f"✓ {column} column exists")
        
        # Check if cpu_affinity column exists in servers table
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
        
        # Check if api_key column exists in users table
        # First ensure users table exists
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
        
        # Now check if api_key column exists
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
                print(f"✓ Migration completed: api_key column added to users table (index may already exist): {e}")
        else:
            print("✓ api_key column exists in users table")
        
        # Check if steam_api_key column exists in users table
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
        
        # Check if steam_account_token column exists in servers table
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
        
        # Check if SSH health tracking columns exist in servers table
        ssh_tracking_columns = ['last_ssh_success', 'last_ssh_failure', 'consecutive_ssh_failures', 'is_ssh_down']
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
                if column == 'last_ssh_success':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN last_ssh_success TIMESTAMP NULL
                        """)
                    )
                elif column == 'last_ssh_failure':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN last_ssh_failure TIMESTAMP NULL
                        """)
                    )
                elif column == 'consecutive_ssh_failures':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN consecutive_ssh_failures INT DEFAULT 0
                        """)
                    )
                elif column == 'is_ssh_down':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN is_ssh_down TINYINT(1) DEFAULT 0
                        """)
                    )
                print(f"✓ Migration completed: {column} column added")
            else:
                print(f"✓ {column} column exists")
        
        # Check if github_proxy column exists in servers table
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
        
        # Check if use_panel_proxy column exists in servers table
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
        
        # Check if SSH health monitoring daemon columns exist in servers table
        ssh_health_columns = ['enable_ssh_health_monitoring', 'ssh_health_check_interval_hours', 
                              'ssh_health_failure_threshold', 'last_ssh_health_check', 'ssh_health_status']
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
                if column == 'enable_ssh_health_monitoring':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN enable_ssh_health_monitoring TINYINT(1) DEFAULT 1
                        """)
                    )
                elif column == 'ssh_health_check_interval_hours':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN ssh_health_check_interval_hours INT DEFAULT 2
                        """)
                    )
                elif column == 'ssh_health_failure_threshold':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN ssh_health_failure_threshold INT DEFAULT 84
                        """)
                    )
                elif column == 'last_ssh_health_check':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN last_ssh_health_check TIMESTAMP NULL
                        """)
                    )
                elif column == 'ssh_health_status':
                    await conn.execute(
                        text("""
                            ALTER TABLE servers 
                            ADD COLUMN ssh_health_status VARCHAR(50) DEFAULT 'unknown'
                        """)
                    )
                print(f"✓ Migration completed: {column} column added")
            else:
                print(f"✓ {column} column exists")
        
        # Check if market_plugins table exists
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
        
        # Check if dependencies column exists in market_plugins table
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
        
        # Fix category enum values if needed (lowercase to uppercase migration)
        # Check current enum definition
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
        
        if category_type and 'game_mode' in category_type[0]:
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
        
        # Check if ssh_servers_sudo table exists
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
        
        # Check if google_id and oauth_provider columns exist in users table
        google_columns = ['google_id', 'oauth_provider']
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
                if column == 'google_id':
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
                        print(f"✓ Migration completed: {column} column added to users table (index may already exist): {index_error}")
                elif column == 'oauth_provider':
                    await conn.execute(
                        text("""
                            ALTER TABLE users 
                            ADD COLUMN oauth_provider VARCHAR(50) NULL
                        """)
                    )
                    print(f"✓ Migration completed: {column} column added to users table")
            else:
                print(f"✓ {column} column exists in users table")
        
        print("✓ Database schema migration completed")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for FastAPI routes to get async database session.
    Uses SQLModel with async SQLAlchemy session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
