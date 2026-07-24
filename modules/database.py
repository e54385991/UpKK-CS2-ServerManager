"""
Database connection and session management (Async)
Using SQLModel for seamless FastAPI integration
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings

engine = create_async_engine(
    settings.mysql_url,
    pool_size=settings.MYSQL_POOL_SIZE,  # Number of connections to keep open
    max_overflow=settings.MYSQL_MAX_OVERFLOW,  # Max overflow connections
    pool_timeout=settings.MYSQL_POOL_TIMEOUT,  # Wait time for connection
    pool_recycle=settings.MYSQL_POOL_RECYCLE,  # Connection recycle time
    pool_pre_ping=settings.MYSQL_POOL_PRE_PING,  # Health check before use
    echo=settings.MYSQL_ECHO,  # Enable/disable SQL query logging
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async_session_maker = AsyncSessionLocal


async def init_db():
    """Compatibility revision check; runtime ``create_all`` is disabled."""
    from cs2_manager.infrastructure.migrations import require_database_current

    await require_database_current(engine)


async def migrate_db():
    """Compatibility wrapper around the advisory-lock Alembic migration."""
    from cs2_manager.infrastructure.migrations import MigrationCoordinator

    await MigrationCoordinator(engine).upgrade()


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
