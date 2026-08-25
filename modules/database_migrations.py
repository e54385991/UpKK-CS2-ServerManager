"""Versioned PostgreSQL migration orchestration shared by startup and CLI."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from alembic import command

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
ALEMBIC_SCRIPT_PATH = PROJECT_ROOT / "alembic"
MIN_POSTGRES_VERSION_NUM = 180000
MIGRATION_ADVISORY_LOCK_KEY = 4851289842802012472


class DatabaseMigrationError(RuntimeError):
    """Raised when the database cannot safely reach the application schema."""


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    server_version_num: int
    current_heads: tuple[str, ...]
    code_heads: tuple[str, ...]

    @property
    def is_current(self) -> bool:
        return self.current_heads == self.code_heads and len(self.code_heads) == 1


def alembic_config(*, connection: Connection | None = None) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_PATH))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def code_heads() -> tuple[str, ...]:
    heads = tuple(ScriptDirectory.from_config(alembic_config()).get_heads())
    if len(heads) != 1:
        raise DatabaseMigrationError(
            f"expected exactly one Alembic head, found {len(heads)}: {heads}"
        )
    return heads


async def _server_version_num(connection: AsyncConnection) -> int:
    raw = await connection.scalar(text("SHOW server_version_num"))
    try:
        version_num = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise DatabaseMigrationError(f"invalid PostgreSQL server_version_num: {raw!r}") from exc
    if version_num < MIN_POSTGRES_VERSION_NUM:
        major = version_num // 10000
        raise DatabaseMigrationError(
            f"PostgreSQL 18+ is required; connected server reports major version {major}"
        )
    return version_num


async def _acquire_migration_lock(connection: AsyncConnection, timeout_seconds: int) -> None:
    if timeout_seconds < 1:
        raise DatabaseMigrationError("DB_MIGRATION_LOCK_TIMEOUT_SECONDS must be at least 1")
    deadline = time.monotonic() + timeout_seconds
    logged_wait = False
    while True:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": MIGRATION_ADVISORY_LOCK_KEY},
        )
        await connection.commit()
        if acquired is True:
            return
        if time.monotonic() >= deadline:
            raise DatabaseMigrationError(
                f"timed out after {timeout_seconds}s waiting for the database migration lock"
            )
        if not logged_wait:
            logger.info("Waiting for the database migration lock held by another process")
            logged_wait = True
        await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


async def _release_migration_lock(connection: AsyncConnection) -> None:
    if connection.in_transaction():
        await connection.rollback()
    released = await connection.scalar(
        text("SELECT pg_advisory_unlock(:key)"),
        {"key": MIGRATION_ADVISORY_LOCK_KEY},
    )
    await connection.commit()
    if released is not True:
        logger.warning("Database migration advisory lock was not held during release")


def _upgrade(sync_connection: Connection) -> None:
    command.upgrade(alembic_config(connection=sync_connection), "head")


def _current_heads(sync_connection: Connection) -> tuple[str, ...]:
    context = MigrationContext.configure(sync_connection)
    return tuple(sorted(context.get_current_heads()))


async def database_status(engine: AsyncEngine) -> DatabaseStatus:
    expected = tuple(sorted(code_heads()))
    async with engine.connect() as connection:
        version_num = await _server_version_num(connection)
        current = await _read_current_heads(connection)
    return DatabaseStatus(version_num, current, expected)


async def _read_current_heads(connection: AsyncConnection) -> tuple[str, ...]:
    current = await connection.run_sync(_current_heads)
    if connection.in_transaction():
        await connection.rollback()
    return current


async def _apply_schema_upgrade(
    engine: AsyncEngine,
    *,
    version_num: int,
    expected: tuple[str, ...],
) -> DatabaseStatus:
    async with engine.connect() as check_connection:
        current = await _read_current_heads(check_connection)
    if current == expected:
        logger.info("Database schema is already current at %s", expected[0])
        return DatabaseStatus(version_num, current, expected)

    logger.info(
        "Upgrading database schema from %s to Alembic head %s",
        ", ".join(current) or "empty",
        expected[0],
    )
    # begin() commits on success. Never read-and-rollback on this connection:
    # a leftover Alembic transaction would otherwise undo the just-applied head.
    async with engine.begin() as work_connection:
        await work_connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        await work_connection.run_sync(_upgrade)
    async with engine.connect() as verify_connection:
        current = await _read_current_heads(verify_connection)
    status = DatabaseStatus(version_num, current, expected)
    if not status.is_current:
        raise DatabaseMigrationError(f"database heads {current} do not match code head {expected}")
    logger.info("Database schema is current at %s", expected[0])
    return status


async def upgrade_database(engine: AsyncEngine, *, lock_timeout_seconds: int) -> DatabaseStatus:
    """Upgrade under one PostgreSQL session lock and fail closed on divergence.

    The advisory lock lives on its own connection so lock release cannot roll
    back a completed schema upgrade if Alembic left a transaction open.
    """
    expected = tuple(sorted(code_heads()))
    async with engine.connect() as lock_connection:
        version_num = await _server_version_num(lock_connection)
        if lock_connection.in_transaction():
            await lock_connection.commit()
        await _acquire_migration_lock(lock_connection, lock_timeout_seconds)
        try:
            return await _apply_schema_upgrade(
                engine,
                version_num=version_num,
                expected=expected,
            )
        finally:
            await _release_migration_lock(lock_connection)


__all__ = [
    "ALEMBIC_CONFIG_PATH",
    "ALEMBIC_SCRIPT_PATH",
    "DatabaseMigrationError",
    "DatabaseStatus",
    "alembic_config",
    "code_heads",
    "database_status",
    "upgrade_database",
]
