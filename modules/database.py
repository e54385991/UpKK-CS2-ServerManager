"""PostgreSQL connection, session and initial-data management."""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    echo=settings.DB_ECHO,
    connect_args={"application_name": "upkk-cs2-server-manager"},
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async_session_maker = AsyncSessionLocal


async def init_db():
    """Seed initial application data after Alembic reaches the current head."""
    from sqlmodel import select

    from .auth import get_password_hash
    from .models import User

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
                is_active=True,
            )
            session.add(admin_user)
            await session.commit()
            print("✓ Default admin user created:")
            print("  Username: admin")
            print("  Password: admin123")
            print("  ⚠️  IMPORTANT: Please change the default password after first login!")


async def migrate_db():
    """Upgrade the PostgreSQL schema to the single current Alembic head."""
    from .database_migrations import upgrade_database

    await upgrade_database(
        engine,
        lock_timeout_seconds=settings.DB_MIGRATION_LOCK_TIMEOUT_SECONDS,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for FastAPI routes to get async database session.
    Uses SQLModel with async SQLAlchemy session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
