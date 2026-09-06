"""MySQL 8 to PostgreSQL 18 integration coverage for the offline migrator."""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Time,
    func,
    select,
    text,
)
from sqlalchemy import (
    Enum as SQLAlchemyEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlmodel import SQLModel

import modules.models  # noqa: F401
import scripts.migrate_mysql_to_postgresql as migration_module
from modules.config import settings
from scripts.migrate_mysql_to_postgresql import (
    LegacyMigrationError,
    TableReport,
    migrate,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LEGACY_MYSQL_INTEGRATION") != "1",
    reason="requires disposable MySQL 8 and PostgreSQL 18+ integration servers",
)


def _legacy_test_metadata() -> MetaData:
    """Build a MySQL-compatible copy used only to model the final legacy schema."""
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table in metadata.tables.values():
        table.indexes.clear()
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()

    servers = metadata.tables["servers"]
    servers.append_column(Column("auto_restart_enabled", Boolean(), nullable=True))
    servers.append_column(Column("monitoring_interval", Integer(), nullable=True))
    servers.append_column(Column("tickrate", Integer(), nullable=True))

    Table(
        "global_settings",
        metadata,
        Column("id", Integer(), primary_key=True, autoincrement=True),
        Column("setting_key", String(100), nullable=False, unique=True),
        Column("setting_value", Text(), nullable=False),
        Column("description", Text(), nullable=True),
        Column("created_at", DateTime(), nullable=True),
        Column("updated_at", DateTime(), nullable=True),
    )
    Table(
        "user_settings",
        metadata,
        Column("id", Integer(), primary_key=True, autoincrement=True),
        Column("user_id", Integer(), ForeignKey("users.id"), nullable=False, unique=True),
        Column("steamcmd_mirror_url", String(500), nullable=True),
        Column("github_api_mirror_url", String(500), nullable=True),
        Column("github_objects_mirror_url", String(500), nullable=True),
        Column("created_at", DateTime(), nullable=True),
        Column("updated_at", DateTime(), nullable=True),
    )
    return metadata


def _placeholder(column, variant: int):
    column_type = column.type
    if isinstance(column_type, SQLAlchemyEnum):
        return column_type.enums[0]
    if isinstance(column_type, Boolean):
        return variant % 2 == 1
    if isinstance(column_type, (Integer, BigInteger)):
        return variant
    if isinstance(column_type, (Float, Numeric)):
        return Decimal(f"{variant}.25")
    if isinstance(column_type, DateTime):
        return datetime(2026, 1, variant, 3, 4, 5)
    if isinstance(column_type, Date):
        return date(2026, 1, variant)
    if isinstance(column_type, Time):
        return time(3, 4, variant)
    if isinstance(column_type, JSON):
        return ["服务器", {"variant": variant}]
    if isinstance(column_type, LargeBinary):
        return f"binary-{variant}".encode()
    string_type = (
        column_type if isinstance(column_type, String) else getattr(column_type, "impl", None)
    )
    if isinstance(string_type, String):
        value = f"{column.table.name[:10]}-{column.name[:10]}-{variant}-服务器"
        return value[: string_type.length] if string_type.length else value
    try:
        python_type = column_type.python_type
    except NotImplementedError:
        python_type = None
    if python_type is str:
        value = f"{column.table.name[:10]}-{column.name[:10]}-{variant}-服务器"
        length = getattr(column_type, "length", None)
        return value[:length] if length else value
    raise AssertionError(f"no legacy fixture value for {column}: {column_type!r}")


def _fixture_row(table, primary_keys, *, variant: int = 1):
    values = {}
    for column in table.columns:
        required_foreign_keys = list(column.foreign_keys)
        if required_foreign_keys and (not column.nullable or column.primary_key):
            referenced = required_foreign_keys[0].column
            values[column.name] = primary_keys[referenced.table.name][referenced.name]
            continue
        if (
            column.primary_key
            and isinstance(column.type, Integer)
            and column.autoincrement in (True, "auto")
            and not column.foreign_keys
        ):
            continue
        if column.default is not None or column.server_default is not None:
            continue
        if column.nullable:
            values[column.name] = None
            continue
        values[column.name] = _placeholder(column, variant)

    if table.name == "plugin_conflict_rules":
        values["plugin_a_id"] = primary_keys["market_plugins"]["id"]
        values["plugin_b_id"] = primary_keys["market_plugins"]["second_id"]
    if table.name == "ai_system_settings":
        values["api_key_encrypted"] = "encrypted-provider-key"
    if table.name == "monitoring_logs":
        values["message"] = "服务器长日志" * 2000
    if table.name == "system_settings":
        values["global_github_token"] = "github-sensitive-token"
    if table.name == "users":
        values.update(
            api_key="CaseSensitiveUserToken",
            steam_api_key="steam-sensitive-token",
            github_token="github-sensitive-token",
            s3_secret_access_key="s3-sensitive-secret",
        )
    if table.name == "servers":
        values.update(
            ssh_password="ssh-sensitive-password",
            api_key="CaseSensitiveServerToken",
            rcon_password="rcon-sensitive-password",
            auto_restart_enabled=True,
            monitoring_interval=45,
            tickrate=128,
        )
    if table.name == "global_settings":
        values.update(
            setting_key="auto_restart_max_restarts",
            setting_value="9",
            description="旧全局配置",
        )
    if table.name == "user_settings":
        values.update(
            steamcmd_mirror_url="https://legacy.example/steamcmd",
            github_api_mirror_url="https://legacy.example/github-api",
            github_objects_mirror_url="https://legacy.example/github-objects",
        )
    return values


async def _seed_every_table(connection: AsyncConnection, metadata: MetaData) -> None:
    primary_keys = {}
    for table in metadata.sorted_tables:
        first_values = _fixture_row(table, primary_keys)
        result = await connection.execute(table.insert().values(first_values))
        primary_keys[table.name] = dict(
            zip(
                (column.name for column in table.primary_key),
                result.inserted_primary_key,
                strict=True,
            )
        )

        if table.name == "market_plugins":
            second_values = _fixture_row(table, primary_keys, variant=2)
            second = await connection.execute(table.insert().values(second_values))
            primary_keys[table.name]["second_id"] = second.inserted_primary_key[0]


async def _create_database(admin_engine, database_name: str) -> None:
    async with admin_engine.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database_name}"'))


async def _drop_database(admin_engine, database_name: str) -> None:
    async with admin_engine.connect() as connection:
        await connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :database_name AND pid <> pg_backend_pid()"
            ),
            {"database_name": database_name},
        )
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))


@pytest.mark.asyncio
async def test_all_tables_copy_verify_sequences_and_roll_back_on_failures(monkeypatch):
    legacy_url = os.environ["LEGACY_MYSQL_DATABASE_URL"]
    assert make_url(legacy_url).drivername == "mysql+aiomysql"
    source_engine = create_async_engine(legacy_url)
    admin_engine = create_async_engine(
        settings.database_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    success_database = f"cs2_legacy_success_{uuid.uuid4().hex}"
    failure_database = f"cs2_legacy_failure_{uuid.uuid4().hex}"
    success_engine = create_async_engine(settings.database_url.set(database=success_database))
    failure_engine = create_async_engine(settings.database_url.set(database=failure_database))
    legacy_metadata = _legacy_test_metadata()

    try:
        await _create_database(admin_engine, success_database)
        await _create_database(admin_engine, failure_database)
        async with source_engine.begin() as connection:
            await connection.run_sync(legacy_metadata.drop_all)
            await connection.run_sync(legacy_metadata.create_all)
            await _seed_every_table(connection, legacy_metadata)

        report = await migrate(source_engine, success_engine)
        assert len(report.tables) == len(SQLModel.metadata.tables)
        assert all(item.rows >= 1 and len(item.sha256) == 64 for item in report.tables)
        assert next(item.rows for item in report.tables if item.table == "market_plugins") == 2
        assert {item.table for item in report.deprecated_artifacts} == {
            "global_settings",
            "user_settings",
            "servers.auto_restart_enabled",
            "servers.monitoring_interval",
            "servers.tickrate",
        }
        assert all(
            item.rows >= 1 and len(item.sha256) == 64 for item in report.deprecated_artifacts
        )

        users = SQLModel.metadata.tables["users"]
        async with success_engine.begin() as connection:
            migrated_maximum = await connection.scalar(select(func.max(users.c.id)))
            inserted = await connection.execute(
                users.insert().values(
                    username="after-migration",
                    email="after@example.com",
                    hashed_password="hash",
                )
            )
            assert inserted.inserted_primary_key[0] > migrated_maximum

        with pytest.raises(LegacyMigrationError, match="must be empty"):
            await migrate(source_engine, success_engine)

        replacement_report = await migrate(
            source_engine,
            success_engine,
            replace_target_data=True,
        )
        assert replacement_report.tables == report.tables
        assert replacement_report.replaced_target_rows == report.total_rows + 1
        async with success_engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(users)
                    .where(users.c.username == "after-migration")
                )
                == 0
            )

        async with success_engine.begin() as connection:
            await connection.execute(
                users.insert().values(
                    username="rollback-sentinel",
                    email="rollback@example.com",
                    hashed_password="hash",
                )
            )

        original_hash = migration_module._hash_target_table

        async def mismatched_hash(connection, table):
            result = await original_hash(connection, table)
            if table.name == "users":
                return TableReport(result.table, result.rows, "0" * 64)
            return result

        monkeypatch.setattr(migration_module, "_hash_target_table", mismatched_hash)
        with pytest.raises(LegacyMigrationError, match="verification failed for tables: users"):
            await migrate(
                source_engine,
                success_engine,
                replace_target_data=True,
            )

        async with success_engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(users)
                    .where(users.c.username == "rollback-sentinel")
                )
                == 1
            )

        with pytest.raises(LegacyMigrationError, match="verification failed for tables: users"):
            await migrate(source_engine, failure_engine)

        async with failure_engine.connect() as connection:
            assert await connection.scalar(select(func.count()).select_from(users)) == 0

        monkeypatch.setattr(migration_module, "_hash_target_table", original_hash)
        original_copy = migration_module._copy_table
        copy_calls = 0

        async def interrupted_copy(*args, **kwargs):
            nonlocal copy_calls
            copy_calls += 1
            if copy_calls == 4:
                raise LegacyMigrationError("simulated copy interruption")
            return await original_copy(*args, **kwargs)

        monkeypatch.setattr(migration_module, "_copy_table", interrupted_copy)
        with pytest.raises(
            LegacyMigrationError,
            match="copy failed for table monitoring_logs: simulated copy interruption",
        ):
            await migrate(source_engine, failure_engine)

        async with failure_engine.connect() as connection:
            assert await connection.scalar(select(func.count()).select_from(users)) == 0

        source_users = legacy_metadata.tables["users"]
        async with source_engine.connect() as connection:
            assert await connection.scalar(select(func.count()).select_from(source_users)) == 1
    finally:
        await success_engine.dispose()
        await failure_engine.dispose()
        await source_engine.dispose()
        await _drop_database(admin_engine, success_database)
        await _drop_database(admin_engine, failure_database)
        await admin_engine.dispose()
