"""Database resource ownership and transaction boundaries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from cs2_manager.core import SettingsProtocol
from cs2_manager.infrastructure.migrations import (
    MigrationCoordinator,
    require_database_current,
)

ConnectionHook = Callable[[AsyncConnection], Awaitable[object]]
LifecycleHook = Callable[[], Awaitable[object]]
SessionFactory = Callable[[], AsyncSession]


class UnitOfWork:
    """Own one session and make transaction completion explicit.

    Exiting without :meth:`commit` rolls the transaction back, which prevents
    repositories from accidentally persisting partial application-service
    work.  A unit of work must not be reused after leaving its context.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None
        self._committed = False

    async def __aenter__(self) -> Self:
        if self.session is not None:
            raise RuntimeError("UnitOfWork is already active")
        self.session = self._session_factory()
        self._committed = False
        return self

    async def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        await self.session.commit()
        self._committed = True

    async def rollback(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        await self.session.rollback()
        self._committed = False

    async def flush(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        await self.session.flush()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self.session is None:
            return
        try:
            if not self._committed:
                await self.session.rollback()
        finally:
            await self.session.close()
            self.session = None


class DatabaseResource:
    """Engine, session factory, revision checks, and readiness for one app."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        migration_runner: ConnectionHook | None = None,
        initialize_schema: bool = False,
    ) -> None:
        self.engine = engine
        self.session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self._migration_runner = migration_runner
        # Retain these constructor arguments for extension compatibility, but
        # never issue create_all from a runtime-owned resource.
        self._initialize_schema = initialize_schema

    @classmethod
    def from_settings(
        cls,
        settings: SettingsProtocol,
        *,
        migration_runner: ConnectionHook | None = None,
        initialize_schema: bool = False,
    ) -> "DatabaseResource":
        engine = create_async_engine(
            settings.mysql_url,
            pool_size=settings.MYSQL_POOL_SIZE,
            max_overflow=settings.MYSQL_MAX_OVERFLOW,
            pool_timeout=settings.MYSQL_POOL_TIMEOUT,
            pool_recycle=settings.MYSQL_POOL_RECYCLE,
            pool_pre_ping=settings.MYSQL_POOL_PRE_PING,
            echo=settings.MYSQL_ECHO,
        )
        return cls(
            engine,
            migration_runner=migration_runner,
            initialize_schema=initialize_schema,
        )

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.session_factory)

    async def migrate(self) -> None:
        """Compatibility hook for explicitly invoked migrations.

        The application lifespan never calls this method. A custom migration
        runner remains supported for tests/extensions; otherwise Alembic owns
        the schema and uses its MySQL advisory lock.
        """
        if self._migration_runner is None:
            await MigrationCoordinator(self.engine).upgrade()
            return
        async with self.engine.begin() as connection:
            await self._migration_runner(connection)

    async def initialize(self) -> None:
        """Compatibility verifier; runtime schema creation is disabled."""
        await require_database_current(self.engine)

    async def require_current(self) -> None:
        await require_database_current(self.engine)

    async def ping(self) -> bool:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True

    async def close(self) -> None:
        await self.engine.dispose()


class LegacyDatabaseResource:
    """Adapter retaining the historical database exports during migration."""

    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.session_factory)

    async def require_current(self) -> None:
        await require_database_current(self.engine)

    async def ping(self) -> bool:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True

    async def close(self) -> None:
        await self.engine.dispose()
