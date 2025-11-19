"""
Database connection and session management (Async)
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from .config import settings
from .models import Base

# Create async database engine
engine = create_async_engine(
    settings.mysql_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DEBUG
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
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database initialized successfully!")
    
    # Create default admin user if no users exist
    from sqlalchemy import select
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
                await conn.run_sync(Base.metadata.create_all)
            
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
        
        print("✓ Database schema migration completed")


async def get_db() -> AsyncSession:
    """Get async database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
