"""Unit contracts for the one-shot legacy MySQL data migrator."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, Table
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel

import scripts.migrate_mysql_to_postgresql as migration_module
from scripts.migrate_mysql_to_postgresql import (
    LegacyMigrationError,
    MigrationReport,
    TableReport,
    TableRowCount,
    _canonical_value,
    _clear_target_tables,
    _expected_tables,
    _legacy_url,
    _normalize_value,
    _parse_args,
    _update_digest,
    _validate_source_schema,
)


def test_legacy_url_is_required_and_must_use_async_mysql(monkeypatch, tmp_path):
    migration_env = tmp_path / ".env"
    migration_env.write_text("")
    monkeypatch.setattr(migration_module, "ENV_FILE", migration_env)
    monkeypatch.delenv("LEGACY_MYSQL_DATABASE_URL", raising=False)
    with pytest.raises(LegacyMigrationError, match="is required"):
        _legacy_url()

    monkeypatch.setenv("LEGACY_MYSQL_DATABASE_URL", "mysql://user:secret@db/source")
    with pytest.raises(LegacyMigrationError, match=r"mysql\+aiomysql"):
        _legacy_url()


def test_legacy_url_can_be_loaded_from_project_env(monkeypatch, tmp_path):
    migration_env = tmp_path / ".env"
    migration_env.write_text(
        "LEGACY_MYSQL_DATABASE_URL='mysql+aiomysql://legacy:secret@mysql:3306/cs2_manager'\n"
    )
    monkeypatch.setattr(migration_module, "ENV_FILE", migration_env)
    monkeypatch.delenv("LEGACY_MYSQL_DATABASE_URL", raising=False)

    assert _legacy_url() == "mysql+aiomysql://legacy:secret@mysql:3306/cs2_manager"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (b"\x00", False),
        (b"\x01", True),
        ("false", False),
        ("TRUE", True),
        (0, False),
        (1, True),
    ],
)
def test_legacy_boolean_normalization(source, expected):
    assert _normalize_value(source, Boolean()) is expected


def test_legacy_json_and_naive_datetime_normalization():
    assert _normalize_value('{"unicode":"服务器"}', JSONB()) == {"unicode": "服务器"}
    aware = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    assert _normalize_value(aware, DateTime()) == aware.replace(tzinfo=None)
    with pytest.raises(LegacyMigrationError, match="not valid JSON"):
        _normalize_value("{broken", JSONB())


def test_canonical_hash_is_deterministic_and_sensitive_to_content():
    row = {"id": 7, "payload": {"b": [2, 1], "a": "服务器"}}
    first = hashlib.sha256()
    second = hashlib.sha256()
    _update_digest(first, row, ("id", "payload"))
    _update_digest(second, row, ("id", "payload"))

    assert first.hexdigest() == second.hexdigest()
    assert _canonical_value(b"secret") == {"bytes_hex": "736563726574"}


def test_incompatible_legacy_schema_fails_before_copying():
    with pytest.raises(LegacyMigrationError, match="missing table"):
        _validate_source_schema(MetaData())


def test_known_deprecated_legacy_artifacts_are_allowed_but_unknown_ones_fail():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        table.to_metadata(metadata)
    metadata.tables["servers"].append_column(
        Column("auto_restart_enabled", Boolean(), nullable=True)
    )
    metadata.tables["servers"].append_column(
        Column("monitoring_interval", Integer(), nullable=True)
    )
    metadata.tables["servers"].append_column(Column("tickrate", Integer(), nullable=True))
    Table("global_settings", metadata, Column("id", Integer(), primary_key=True))
    Table("user_settings", metadata, Column("id", Integer(), primary_key=True))

    _validate_source_schema(metadata)

    Table("unknown_legacy_table", metadata, Column("id", Integer(), primary_key=True))
    with pytest.raises(LegacyMigrationError, match="unexpected tables"):
        _validate_source_schema(metadata)


def test_report_contains_only_counts_and_digests():
    report = MigrationReport(
        (TableReport("users", 2, "a" * 64),),
        replaced_target=(TableRowCount("users", 1),),
    )
    payload = json.loads(report.as_json())

    assert payload == {
        "success": True,
        "total_rows": 2,
        "tables": [
            {
                "table": "users",
                "rows": 2,
                "sha256": "a" * 64,
                "primary_key_min": None,
                "primary_key_max": None,
            }
        ],
        "deprecated_artifacts": [],
        "replaced_target_rows": 1,
        "replaced_target_tables": [{"table": "users", "rows": 1}],
    }


@pytest.mark.asyncio
async def test_target_replacement_clears_every_table_in_reverse_dependency_order():
    tables = _expected_tables()
    connection = AsyncMock()
    connection.scalar.side_effect = [1 if table.name == "users" else 0 for table in tables]

    replaced = await _clear_target_tables(connection)

    assert replaced == (TableRowCount("users", 1),)
    deleted_tables = [call.args[0].table.name for call in connection.execute.await_args_list]
    assert deleted_tables == [table.name for table in reversed(tables)]


def test_target_replacement_requires_explicit_cli_switch():
    assert _parse_args([]).replace_target_data is False
    assert _parse_args(["--replace-target-data"]).replace_target_data is True
