#!/usr/bin/env python3
"""Copy a normalized legacy MySQL database into an empty PostgreSQL schema."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

from dotenv import dotenv_values
from sqlalchemy import JSON, Boolean, DateTime, MetaData, Table, func, select, text
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql.sqltypes import Integer
from sqlmodel import SQLModel

import modules.models  # noqa: F401
from modules.config import ENV_FILE, settings
from modules.database import engine as target_engine
from modules.database_migrations import upgrade_database

BATCH_SIZE = 1000
LEGACY_URL_ENV = "LEGACY_MYSQL_DATABASE_URL"
KNOWN_DEPRECATED_TABLE_COLUMNS = {
    "global_settings": {
        "id",
        "setting_key",
        "setting_value",
        "description",
        "created_at",
        "updated_at",
    },
    "user_settings": {
        "id",
        "user_id",
        "steamcmd_mirror_url",
        "github_api_mirror_url",
        "github_objects_mirror_url",
        "created_at",
        "updated_at",
    },
}
KNOWN_DEPRECATED_COLUMNS = {
    "servers": {"auto_restart_enabled", "monitoring_interval", "tickrate"},
}


class LegacyMigrationError(RuntimeError):
    """Raised when an offline migration cannot complete without data risk."""


@dataclass(frozen=True, slots=True)
class TableReport:
    table: str
    rows: int
    sha256: str
    primary_key_min: Any = None
    primary_key_max: Any = None


@dataclass(frozen=True, slots=True)
class MigrationReport:
    tables: tuple[TableReport, ...]
    deprecated_artifacts: tuple[TableReport, ...] = ()

    @property
    def total_rows(self) -> int:
        return sum(item.rows for item in self.tables)

    def as_json(self) -> str:
        return json.dumps(
            {
                "success": True,
                "total_rows": self.total_rows,
                "tables": [asdict(item) for item in self.tables],
                "deprecated_artifacts": [
                    asdict(item) for item in self.deprecated_artifacts
                ],
            },
            ensure_ascii=False,
            indent=2,
        )


def _legacy_url() -> str:
    env_file_value = dotenv_values(ENV_FILE).get(LEGACY_URL_ENV)
    raw = (os.getenv(LEGACY_URL_ENV) or env_file_value or "").strip()
    if not raw:
        raise LegacyMigrationError(f"{LEGACY_URL_ENV} is required")
    url = make_url(raw)
    if url.drivername != "mysql+aiomysql":
        raise LegacyMigrationError(f"{LEGACY_URL_ENV} must use the mysql+aiomysql driver")
    if not url.database:
        raise LegacyMigrationError(f"{LEGACY_URL_ENV} must include a database name")
    return raw


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, (datetime, time)):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _update_digest(digest: Any, row: dict[str, Any], column_names: tuple[str, ...]) -> None:
    payload = [_canonical_value(row[name]) for name in column_names]
    digest.update(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    )
    digest.update(b"\n")


def _normalize_value(value: Any, target_type: Any) -> Any:
    if value is None:
        return None
    if isinstance(target_type, Boolean):
        if isinstance(value, (bytes, bytearray)):
            return int.from_bytes(value, byteorder="big") != 0
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"0", "false", "no", "off", ""}:
                return False
            if normalized in {"1", "true", "yes", "on"}:
                return True
            raise LegacyMigrationError("legacy boolean value is invalid")
        return bool(value)
    if isinstance(target_type, SQLAlchemyEnum):
        if isinstance(value, Enum):
            return value.name
        return str(value)
    if isinstance(target_type, (JSON, JSONB)):
        if isinstance(value, (str, bytes, bytearray)):
            try:
                return json.loads(value)
            except (TypeError, ValueError) as exc:
                raise LegacyMigrationError("legacy JSON value is not valid JSON") from exc
        return value
    if isinstance(target_type, DateTime) and isinstance(value, datetime) and value.tzinfo:
        return value.replace(tzinfo=None)
    return value


def _normalize_row(row: Any, target_table: Table) -> dict[str, Any]:
    return {
        column.name: _normalize_value(row[column.name], column.type)
        for column in target_table.columns
    }


async def _reflect(connection: AsyncConnection) -> MetaData:
    metadata = MetaData()

    def reflect(sync_connection: Any) -> None:
        metadata.reflect(bind=sync_connection)

    await connection.run_sync(reflect)
    return metadata


def _expected_tables() -> tuple[Table, ...]:
    return tuple(SQLModel.metadata.sorted_tables)


def _validate_source_schema(source_metadata: MetaData) -> None:
    failures: list[str] = []
    expected_tables = _expected_tables()
    expected_names = {table.name for table in expected_tables}
    deprecated_table_names = set(KNOWN_DEPRECATED_TABLE_COLUMNS)
    unexpected_tables = sorted(
        set(source_metadata.tables) - expected_names - deprecated_table_names
    )
    if unexpected_tables:
        failures.append(f"unexpected tables {unexpected_tables}")
    for table_name, allowed_columns in KNOWN_DEPRECATED_TABLE_COLUMNS.items():
        deprecated_table = source_metadata.tables.get(table_name)
        if deprecated_table is None:
            continue
        unexpected_columns = sorted(
            set(deprecated_table.columns.keys()) - allowed_columns
        )
        if unexpected_columns:
            failures.append(
                f"{table_name} has unknown legacy columns {unexpected_columns}"
            )
    for expected in expected_tables:
        source = source_metadata.tables.get(expected.name)
        if source is None:
            failures.append(f"missing table {expected.name}")
            continue
        source_columns = set(source.columns.keys())
        missing_columns = [name for name in expected.columns.keys() if name not in source_columns]
        unexpected_columns = source_columns - set(expected.columns.keys())
        unsupported_columns = sorted(
            unexpected_columns - KNOWN_DEPRECATED_COLUMNS.get(expected.name, set())
        )
        if missing_columns:
            failures.append(f"{expected.name} missing columns {missing_columns}")
        if unsupported_columns:
            failures.append(
                f"{expected.name} unexpected columns {unsupported_columns}"
            )
        source_primary_key = tuple(column.name for column in source.primary_key)
        expected_primary_key = tuple(column.name for column in expected.primary_key)
        if source_primary_key != expected_primary_key:
            failures.append(
                f"{expected.name} primary key {source_primary_key} does not match "
                f"{expected_primary_key}"
            )
    if failures:
        details = "; ".join(failures)
        raise LegacyMigrationError(
            "legacy MySQL schema is not at the final supported revision: " + details
        )


def _validate_target_schema(target_metadata: MetaData) -> None:
    expected = {table.name: set(table.columns.keys()) for table in _expected_tables()}
    actual_names = set(target_metadata.tables)
    allowed_names = set(expected) | {"alembic_version"}
    failures = []
    if missing := sorted(set(expected) - actual_names):
        failures.append(f"missing tables {missing}")
    if unexpected := sorted(actual_names - allowed_names):
        failures.append(f"unexpected tables {unexpected}")
    for table_name, expected_columns in expected.items():
        actual = target_metadata.tables.get(table_name)
        if actual is None:
            continue
        if set(actual.columns.keys()) != expected_columns:
            failures.append(f"{table_name} columns do not match the Alembic head")
    if failures:
        raise LegacyMigrationError(
            "target PostgreSQL schema must contain only the current Alembic structure: "
            + "; ".join(failures)
        )


async def _validate_empty_target(connection: AsyncConnection) -> None:
    non_empty: list[str] = []
    for table in _expected_tables():
        count = int((await connection.scalar(select(func.count()).select_from(table))) or 0)
        if count:
            non_empty.append(f"{table.name}={count}")
    if non_empty:
        raise LegacyMigrationError(
            "target PostgreSQL application tables must be empty: " + ", ".join(non_empty)
        )


def _order_columns(table: Table) -> tuple[Any, ...]:
    primary_key = tuple(table.primary_key.columns)
    return primary_key or tuple(table.columns)


def _primary_key_marker(row: dict[str, Any], table: Table) -> Any:
    values = [_canonical_value(row[column.name]) for column in table.primary_key]
    if not values:
        return None
    return values[0] if len(values) == 1 else values


async def _hash_projection(
    connection: AsyncConnection,
    table: Table,
    columns: tuple[Any, ...],
    artifact_name: str,
) -> TableReport:
    column_names = tuple(column.name for column in columns)
    stream = await connection.stream(
        select(*columns).order_by(*_order_columns(table))
    )
    mappings = stream.mappings()
    digest = hashlib.sha256()
    row_count = 0
    primary_key_min = None
    primary_key_max = None
    while True:
        rows = await mappings.fetchmany(BATCH_SIZE)
        if not rows:
            break
        for row in rows:
            values = dict(row)
            _update_digest(digest, values, column_names)
            marker = _primary_key_marker(values, table)
            if primary_key_min is None:
                primary_key_min = marker
            primary_key_max = marker
        row_count += len(rows)
    return TableReport(
        artifact_name,
        row_count,
        digest.hexdigest(),
        primary_key_min,
        primary_key_max,
    )


async def _deprecated_artifact_reports(
    connection: AsyncConnection,
    source_metadata: MetaData,
) -> tuple[TableReport, ...]:
    reports = []
    for table_name in sorted(KNOWN_DEPRECATED_TABLE_COLUMNS):
        table = source_metadata.tables.get(table_name)
        if table is None:
            continue
        reports.append(
            await _hash_projection(
                connection,
                table,
                tuple(table.columns),
                table_name,
            )
        )
    for table_name, column_names in sorted(KNOWN_DEPRECATED_COLUMNS.items()):
        table = source_metadata.tables.get(table_name)
        if table is None:
            continue
        primary_key_columns = tuple(table.primary_key.columns)
        for column_name in sorted(column_names):
            if column_name not in table.columns:
                continue
            projection = (*primary_key_columns, table.c[column_name])
            reports.append(
                await _hash_projection(
                    connection,
                    table,
                    projection,
                    f"{table_name}.{column_name}",
                )
            )
    return tuple(reports)


async def _copy_table(
    source: AsyncConnection,
    target: AsyncConnection,
    source_table: Table,
    target_table: Table,
) -> TableReport:
    column_names = tuple(column.name for column in target_table.columns)
    source_columns = [source_table.c[name] for name in column_names]
    statement = select(*source_columns).order_by(*_order_columns(source_table))
    stream = await source.stream(statement)
    mappings = stream.mappings()
    digest = hashlib.sha256()
    row_count = 0
    primary_key_min = None
    primary_key_max = None
    while True:
        rows = await mappings.fetchmany(BATCH_SIZE)
        if not rows:
            break
        normalized = [_normalize_row(row, target_table) for row in rows]
        for row in normalized:
            _update_digest(digest, row, column_names)
            marker = _primary_key_marker(row, target_table)
            if primary_key_min is None:
                primary_key_min = marker
            primary_key_max = marker
        await target.execute(target_table.insert(), normalized)
        row_count += len(normalized)
    return TableReport(
        target_table.name,
        row_count,
        digest.hexdigest(),
        primary_key_min,
        primary_key_max,
    )


async def _hash_target_table(
    connection: AsyncConnection,
    table: Table,
) -> TableReport:
    column_names = tuple(column.name for column in table.columns)
    stream = await connection.stream(select(table).order_by(*_order_columns(table)))
    mappings = stream.mappings()
    digest = hashlib.sha256()
    row_count = 0
    primary_key_min = None
    primary_key_max = None
    while True:
        rows = await mappings.fetchmany(BATCH_SIZE)
        if not rows:
            break
        for row in rows:
            normalized = _normalize_row(row, table)
            _update_digest(digest, normalized, column_names)
            marker = _primary_key_marker(normalized, table)
            if primary_key_min is None:
                primary_key_min = marker
            primary_key_max = marker
        row_count += len(rows)
    return TableReport(
        table.name,
        row_count,
        digest.hexdigest(),
        primary_key_min,
        primary_key_max,
    )


async def _reset_sequences(connection: AsyncConnection) -> None:
    for table in _expected_tables():
        try:
            primary_key = tuple(table.primary_key.columns)
            if len(primary_key) != 1 or not isinstance(primary_key[0].type, Integer):
                continue
            column = primary_key[0]
            sequence = await connection.scalar(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": table.name, "column_name": column.name},
            )
            if not sequence:
                continue
            maximum = await connection.scalar(select(func.max(column)))
            await connection.execute(
                text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"),
                {
                    "sequence": sequence,
                    "value": int(maximum) if maximum is not None else 1,
                    "called": maximum is not None,
                },
            )
        except Exception as exc:
            raise LegacyMigrationError(
                f"sequence calibration failed for table {table.name}"
            ) from exc


async def migrate(source_engine: AsyncEngine, target: AsyncEngine) -> MigrationReport:
    """Copy all application rows while leaving the legacy source untouched."""
    await upgrade_database(
        target,
        lock_timeout_seconds=settings.DB_MIGRATION_LOCK_TIMEOUT_SECONDS,
    )
    async with source_engine.connect() as source, target.connect() as destination:
        source_metadata = await _reflect(source)
        target_metadata = await _reflect(destination)
        if source.in_transaction():
            await source.rollback()
        if destination.in_transaction():
            await destination.rollback()
        _validate_source_schema(source_metadata)
        _validate_target_schema(target_metadata)

        await _validate_empty_target(destination)
        if destination.in_transaction():
            await destination.rollback()

        source = await source.execution_options(isolation_level="REPEATABLE READ")
        async with source.begin(), destination.begin():
            try:
                deprecated_reports = await _deprecated_artifact_reports(
                    source, source_metadata
                )
            except Exception as exc:
                raise LegacyMigrationError(
                    "failed to audit known deprecated MySQL artifacts: "
                    f"{exc.__class__.__name__}"
                ) from exc
            source_reports: list[TableReport] = []
            for target_table in _expected_tables():
                try:
                    source_reports.append(
                        await _copy_table(
                            source,
                            destination,
                            source_metadata.tables[target_table.name],
                            target_table,
                        )
                    )
                except LegacyMigrationError as exc:
                    raise LegacyMigrationError(
                        f"copy failed for table {target_table.name}: {exc}"
                    ) from exc
                except Exception as exc:
                    raise LegacyMigrationError(
                        f"copy failed for table {target_table.name}: {exc.__class__.__name__}"
                    ) from exc

            await _reset_sequences(destination)
            target_reports = []
            for table in _expected_tables():
                try:
                    target_reports.append(await _hash_target_table(destination, table))
                except LegacyMigrationError as exc:
                    raise LegacyMigrationError(
                        f"verification read failed for table {table.name}: {exc}"
                    ) from exc
                except Exception as exc:
                    raise LegacyMigrationError(
                        f"verification read failed for table {table.name}: {exc.__class__.__name__}"
                    ) from exc
            if source_reports != target_reports:
                source_by_table = {item.table: item for item in source_reports}
                target_by_table = {item.table: item for item in target_reports}
                failed = [
                    name
                    for name in source_by_table
                    if source_by_table[name] != target_by_table.get(name)
                ]
                raise LegacyMigrationError(
                    "target verification failed for tables: " + ", ".join(failed)
                )

        return MigrationReport(tuple(source_reports), deprecated_reports)


async def async_main() -> int:
    source_engine = create_async_engine(
        _legacy_url(),
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
    )
    try:
        report = await migrate(source_engine, target_engine)
        print(report.as_json())
        return 0
    except LegacyMigrationError as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    except SQLAlchemyError as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": f"database operation failed: {exc.__class__.__name__}",
                },
                ensure_ascii=False,
            )
        )
        return 1
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
