"""Unit contracts for the one-shot legacy MySQL data migrator."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import Boolean, DateTime, MetaData
from sqlalchemy.dialects.postgresql import JSONB

import scripts.migrate_mysql_to_postgresql as migration_module
from scripts.migrate_mysql_to_postgresql import (
    LegacyMigrationError,
    MigrationReport,
    TableReport,
    _canonical_value,
    _legacy_url,
    _normalize_value,
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


def test_report_contains_only_counts_and_digests():
    report = MigrationReport((TableReport("users", 2, "a" * 64),))
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
    }
