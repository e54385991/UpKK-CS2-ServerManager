"""Unit coverage for the ordered legacy migration registry."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from modules import migrations
from modules.migrations import MIGRATION_STEPS, MigrationStep
from modules.migrations.common import column_exists, table_exists
from modules.migrations.plugins import migrate_plugins


def test_migration_step_order_preserves_the_legacy_sequence():
    assert tuple(step.name for step in MIGRATION_STEPS) == (
        "bootstrap",
        "server_core",
        "server_features",
        "plugins",
        "accounts",
        "commands",
        "system_settings",
        "ai",
    )


@pytest.mark.asyncio
async def test_migration_runner_is_repeatable_and_ordered(monkeypatch):
    calls: list[str] = []

    async def first(_conn):
        calls.append("first")

    async def second(_conn):
        calls.append("second")

    monkeypatch.setattr(
        migrations,
        "MIGRATION_STEPS",
        (MigrationStep("first", first), MigrationStep("second", second)),
    )

    await migrations.run_migrations(object())
    await migrations.run_migrations(object())

    assert calls == ["first", "second", "first", "second"]


@dataclass
class _Result:
    row: object | None

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    async def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))
        return _Result(next(self.rows))


@pytest.mark.asyncio
async def test_metadata_helpers_cover_existing_and_missing_objects():
    connection = _Connection([("servers",), None])

    assert await table_exists(connection, "servers") is True
    assert await column_exists(connection, "servers", "missing") is False
    assert connection.calls[0][1] == {"table": "servers"}
    assert connection.calls[1][1] == {
        "table": "servers",
        "column": "missing",
    }


class _ExistingPluginSchemaConnection:
    def __init__(self):
        self.calls = []

    async def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return _Result(("exists",))


@pytest.mark.asyncio
async def test_plugin_migration_replaces_advertisement_default_with_two_roots():
    connection = _ExistingPluginSchemaConnection()

    await migrate_plugins(connection)

    inserts = [
        parameters
        for sql, parameters in connection.calls
        if "INSERT INTO plugin_config_sources" in sql
    ]
    assert inserts == [
        {
            "relative_path": "cs2/game/csgo/addons/counterstrikesharp/configs",
            "digest": "ffc956aa71ba63427df3e0c25b9bc7b2a233a59c16fba95c8fa08eeb6c58c590",
        },
        {
            "relative_path": "cs2/game/csgo/cfg",
            "digest": "554abb15735aeb89dc5f6f6ab0deef3c5ee3fa8bbce7eddd755d4802ecfb0469",
        },
    ]
    migration_sql = "\n".join(sql for sql, _parameters in connection.calls)
    assert "old_source.is_enabled = 0" in migration_sql
    assert (
        "old_source.relative_path = 'cs2/game/csgo/addons/counterstrikesharp/configs'"
        in migration_sql
    )
