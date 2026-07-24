"""Real MySQL 8 integration coverage for the Alembic migration lifecycle."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import URL, Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from cs2_manager.infrastructure import migrations
from cs2_manager.infrastructure.credentials import CredentialCipher, hash_token

RUN_MYSQL_INTEGRATION = os.getenv("RUN_INTEGRATION_TESTS") == "1"
MYSQL_ADMIN_USER = os.getenv("MYSQL_ADMIN_USER")
MYSQL_ADMIN_PASSWORD = os.getenv("MYSQL_ADMIN_PASSWORD")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (RUN_MYSQL_INTEGRATION and MYSQL_ADMIN_USER and MYSQL_ADMIN_PASSWORD),
        reason=(
            "set RUN_INTEGRATION_TESTS=1 and provide "
            "MYSQL_ADMIN_USER/MYSQL_ADMIN_PASSWORD to run MySQL migration tests"
        ),
    ),
]

REVISION_0001 = "0001_legacy_baseline"
REVISION_0002 = "0002_credential_security_expand"
REVISION_0003 = "0003_credential_data_backfill"
_TEMP_DATABASE_PATTERN = re.compile(r"^cs2mgr_it_[0-9a-f]{16}$")

_LEGACY_CREDENTIALS: dict[str, dict[str, str]] = {
    "initialized_servers": {"ssh_password": "initialized-ssh-password"},
    "servers": {
        "api_key": "server-api-key",
        "ssh_password": "server-ssh-password",
        "sudo_password": "server-sudo-password",
        "server_password": "server-game-password",
        "rcon_password": "server-rcon-password",
        "steam_account_token": "server-gslt",
        "discord_webhook_url": "https://discord.invalid/integration-hook",
    },
    "ssh_servers_sudo": {"sudo_password": "wizard-sudo-password"},
    "system_settings": {
        "global_github_token": "global-github-token",
        "gmail_credentials_json": '{"client_secret":"gmail-secret"}',
        "gmail_token_json": '{"access_token":"gmail-token"}',
        "smtp_password": "smtp-password",
    },
    "users": {
        "steam_api_key": "steam-api-key",
        "github_token": "user-github-token",
        "s3_access_key_id": "s3-access-key",
        "s3_secret_access_key": "s3-secret-key",
    },
}
_USER_API_KEY = "user-api-key"
_RESET_TOKEN = "password-reset-token"


@dataclass(frozen=True, slots=True)
class MySQLTestDatabase:
    name: str
    url: str
    engine: AsyncEngine


def _mysql_url(*, username: str, password: str, database: str) -> URL:
    return URL.create(
        "mysql+aiomysql",
        username=username,
        password=password,
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=database,
        query={"charset": "utf8mb4"},
    )


def _quoted_temporary_database(name: str) -> str:
    """Validate the generated identifier immediately before privileged DDL."""
    if not _TEMP_DATABASE_PATTERN.fullmatch(name):
        raise ValueError(f"refusing database DDL for unsafe integration database name: {name!r}")
    return f"`{name}`"


@pytest.fixture
async def mysql_test_database() -> AsyncIterator[MySQLTestDatabase]:
    assert MYSQL_ADMIN_USER is not None
    assert MYSQL_ADMIN_PASSWORD is not None
    name = f"cs2mgr_it_{uuid4().hex[:16]}"
    identifier = _quoted_temporary_database(name)
    admin_engine = create_async_engine(
        _mysql_url(
            username=MYSQL_ADMIN_USER,
            password=MYSQL_ADMIN_PASSWORD,
            database="mysql",
        ),
        poolclass=NullPool,
        pool_pre_ping=True,
    )
    database_engine: AsyncEngine | None = None
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(
                sa.text(
                    f"CREATE DATABASE {identifier} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            created = True
            await connection.commit()

        database_url = _mysql_url(
            username=MYSQL_ADMIN_USER,
            password=MYSQL_ADMIN_PASSWORD,
            database=name,
        ).render_as_string(hide_password=False)
        database_engine = create_async_engine(database_url, pool_pre_ping=True)
        yield MySQLTestDatabase(name=name, url=database_url, engine=database_engine)
    finally:
        if database_engine is not None:
            await database_engine.dispose()
        try:
            if created:
                # Revalidate rather than retaining an unchecked interpolation path
                # in teardown, where a failed test must never broaden DROP scope.
                identifier = _quoted_temporary_database(name)
                async with admin_engine.connect() as connection:
                    await connection.execute(sa.text(f"DROP DATABASE IF EXISTS {identifier}"))
                    await connection.commit()
        finally:
            await admin_engine.dispose()


def _alembic_config(connection: Connection) -> Config:
    config = Config(str(migrations.ALEMBIC_CONFIG_PATH))
    config.set_main_option("script_location", str(migrations.ALEMBIC_SCRIPT_PATH))
    config.attributes["connection"] = connection
    return config


async def _run_alembic_command(
    engine: AsyncEngine,
    operation: Callable[[Config, str], None],
    revision: str,
) -> None:
    def run(sync_connection: Connection) -> None:
        operation(_alembic_config(sync_connection), revision)

    async with engine.connect() as connection:
        await connection.run_sync(run)
        if connection.in_transaction():
            await connection.commit()


def _required_column_value(column: sa.Column[object]) -> object:
    type_ = column.type
    if isinstance(type_, sa.Enum):
        return type_.enums[0]
    if isinstance(type_, sa.DateTime):
        return datetime(2035, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    if isinstance(type_, sa.Boolean):
        return False
    if isinstance(type_, sa.Integer):
        return 1
    if isinstance(type_, sa.Float):
        return 1.0
    if isinstance(type_, sa.JSON):
        return {}
    if isinstance(type_, (sa.String, sa.Text)):
        return "x"
    raise AssertionError(f"no integration seed value for {column.table.name}.{column.name}")


async def _insert_required_row(
    connection: AsyncConnection,
    table_name: str,
    overrides: dict[str, object],
) -> None:
    metadata = sa.MetaData()

    def reflect(sync_connection: Connection) -> sa.Table:
        return sa.Table(table_name, metadata, autoload_with=sync_connection)

    table = await connection.run_sync(reflect)
    values: dict[str, object] = {}
    for column in table.columns:
        if column.name in overrides:
            values[column.name] = overrides[column.name]
        elif column.nullable or column.server_default is not None:
            continue
        elif column.primary_key and column.autoincrement:
            continue
        else:
            values[column.name] = _required_column_value(column)
    await connection.execute(table.insert().values(**values))


async def _seed_legacy_credentials(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await _insert_required_row(
            connection,
            "system_settings",
            {"id": 1, **_LEGACY_CREDENTIALS["system_settings"]},
        )
        await _insert_required_row(
            connection,
            "users",
            {
                "id": 1,
                "username": "integration-user",
                "email": "integration@example.invalid",
                "hashed_password": "unused-password-hash",
                "api_key": _USER_API_KEY,
                **_LEGACY_CREDENTIALS["users"],
            },
        )
        await _insert_required_row(
            connection,
            "initialized_servers",
            {
                "id": 1,
                "user_id": 1,
                "name": "initialized-server",
                **_LEGACY_CREDENTIALS["initialized_servers"],
            },
        )
        await _insert_required_row(
            connection,
            "password_reset_tokens",
            {"id": 1, "user_id": 1, "token": _RESET_TOKEN},
        )
        await _insert_required_row(
            connection,
            "servers",
            {
                "id": 1,
                "user_id": 1,
                "name": "managed-server",
                "server_name": "integration-cs2",
                **_LEGACY_CREDENTIALS["servers"],
            },
        )
        await _insert_required_row(
            connection,
            "ssh_servers_sudo",
            {"id": 1, "user_id": 1, **_LEGACY_CREDENTIALS["ssh_servers_sudo"]},
        )


def _integration_cipher() -> CredentialCipher:
    cipher = CredentialCipher.from_settings(
        SimpleNamespace(
            CREDENTIAL_ENCRYPTION_KEYS=os.getenv("CREDENTIAL_ENCRYPTION_KEYS", ""),
            CREDENTIAL_ACTIVE_KEY_ID=os.getenv("CREDENTIAL_ACTIVE_KEY_ID", ""),
        )
    )
    assert cipher.enabled, "integration migrations require a configured AES-256-GCM test key"
    return cipher


def _integration_token_hash_key() -> str:
    key = os.getenv("TOKEN_HASH_KEY") or os.getenv("SECRET_KEY")
    assert key, "integration migrations require TOKEN_HASH_KEY or SECRET_KEY"
    return key


async def _assert_credential_state(
    engine: AsyncEngine,
    *,
    encrypted: bool,
    hashes_backfilled: bool = True,
) -> str:
    cipher = _integration_cipher()
    async with engine.connect() as connection:
        server_envelope = ""
        for table_name, columns in _LEGACY_CREDENTIALS.items():
            for column_name, plaintext in columns.items():
                row = (
                    (
                        await connection.execute(
                            sa.text(
                                f"SELECT `{column_name}`, `{column_name}_encrypted` "
                                f"FROM `{table_name}` WHERE id = 1"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                stored = row[column_name]
                shadow = row[f"{column_name}_encrypted"]
                assert stored == plaintext
                if encrypted:
                    assert isinstance(shadow, str)
                    assert cipher.is_encrypted(shadow), (
                        f"{table_name}.{column_name}_encrypted is plaintext"
                    )
                    assert cipher.decrypt(shadow, aad=f"{table_name}:1:{column_name}") == plaintext
                    if (table_name, column_name) == ("servers", "api_key"):
                        server_envelope = shadow
                else:
                    assert shadow is None

        token_hash_key = _integration_token_hash_key()
        user = (
            (
                await connection.execute(
                    sa.text("SELECT api_key, api_key_hash, api_key_prefix FROM users WHERE id = 1")
                )
            )
            .mappings()
            .one()
        )
        assert user["api_key"] == _USER_API_KEY
        expected_user_hash = hash_token(_USER_API_KEY, token_hash_key)
        assert user["api_key_hash"] == (expected_user_hash if hashes_backfilled else None)
        assert user["api_key_prefix"] == (_USER_API_KEY[:8] if hashes_backfilled else None)

        reset = (
            (
                await connection.execute(
                    sa.text(
                        "SELECT token, token_hash, token_prefix "
                        "FROM password_reset_tokens WHERE id = 1"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert reset["token"] == _RESET_TOKEN
        expected_reset_hash = hash_token(_RESET_TOKEN, token_hash_key)
        assert reset["token_hash"] == (expected_reset_hash if hashes_backfilled else None)
        assert reset["token_prefix"] == (_RESET_TOKEN[:8] if hashes_backfilled else None)

        server_hash = await connection.scalar(
            sa.text("SELECT api_key_hash FROM servers WHERE id = 1")
        )
        expected_server_hash = hash_token(
            _LEGACY_CREDENTIALS["servers"]["api_key"],
            token_hash_key,
        )
        assert server_hash == (expected_server_hash if hashes_backfilled else None)
        return server_envelope


@pytest.mark.asyncio
async def test_empty_database_upgrade_is_current_and_repeatable(
    mysql_test_database: MySQLTestDatabase,
) -> None:
    first = await migrations.migrate_database(mysql_test_database.url)
    second = await migrations.migrate_database(mysql_test_database.url)
    checked = await migrations.require_database_current(mysql_test_database.engine)

    assert first.current_revisions == (REVISION_0003,)
    assert second == first
    assert checked == first
    async with mysql_test_database.engine.connect() as connection:
        table_names = {
            str(name)
            for name in (
                await connection.execute(
                    sa.text(
                        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA = DATABASE()"
                    )
                )
            ).scalars()
        }
        version_rows = (
            (await connection.execute(sa.text("SELECT version_num FROM alembic_version")))
            .scalars()
            .all()
        )
    assert migrations.MANAGED_TABLES <= table_names
    assert version_rows == [REVISION_0003]


@pytest.mark.asyncio
async def test_concurrent_upgrades_wait_for_the_real_mysql_advisory_lock(
    mysql_test_database: MySQLTestDatabase,
) -> None:
    tasks: list[asyncio.Task[migrations.MigrationStatus]] = []
    async with mysql_test_database.engine.connect() as lock_connection:
        async with migrations._mysql_advisory_lock(  # noqa: SLF001
            lock_connection,
            timeout_seconds=5,
        ):
            tasks = [
                asyncio.create_task(
                    migrations.migrate_database(
                        mysql_test_database.url,
                        lock_timeout_seconds=10,
                    )
                )
                for _ in range(2)
            ]
            await asyncio.sleep(0.1)
            blocked_while_lock_was_held = all(not task.done() for task in tasks)

    statuses = await asyncio.gather(*tasks)
    assert blocked_while_lock_was_held
    assert all(status.current_revisions == (REVISION_0003,) for status in statuses)
    status = await migrations.get_migration_status(mysql_test_database.engine)
    assert status.is_current


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_revision", [REVISION_0001, REVISION_0002])
async def test_historical_partial_revisions_upgrade_to_current(
    mysql_test_database: MySQLTestDatabase,
    historical_revision: str,
) -> None:
    await _run_alembic_command(mysql_test_database.engine, command.upgrade, historical_revision)
    before = await migrations.get_migration_status(mysql_test_database.engine)
    assert before.current_revisions == (historical_revision,)
    assert not before.is_current

    after = await migrations.migrate_database(mysql_test_database.url)
    assert after.current_revisions == (REVISION_0003,)
    assert after.is_current


@pytest.mark.asyncio
async def test_credential_backfill_downgrade_restore_and_reupgrade(
    mysql_test_database: MySQLTestDatabase,
) -> None:
    await _run_alembic_command(mysql_test_database.engine, command.upgrade, REVISION_0001)
    await _seed_legacy_credentials(mysql_test_database.engine)
    await _run_alembic_command(mysql_test_database.engine, command.upgrade, REVISION_0002)
    partial = await migrations.get_migration_status(mysql_test_database.engine)
    assert partial.current_revisions == (REVISION_0002,)
    await _assert_credential_state(
        mysql_test_database.engine,
        encrypted=False,
        hashes_backfilled=False,
    )

    upgraded = await migrations.migrate_database(mysql_test_database.url)
    assert upgraded.current_revisions == (REVISION_0003,)
    first_envelope = await _assert_credential_state(mysql_test_database.engine, encrypted=True)

    await _run_alembic_command(mysql_test_database.engine, command.downgrade, REVISION_0002)
    downgraded = await migrations.get_migration_status(mysql_test_database.engine)
    assert downgraded.current_revisions == (REVISION_0002,)
    await _assert_credential_state(mysql_test_database.engine, encrypted=False)

    restored = await migrations.migrate_database(mysql_test_database.url)
    assert restored.current_revisions == (REVISION_0003,)
    restored_envelope = await _assert_credential_state(
        mysql_test_database.engine,
        encrypted=True,
    )
    assert restored_envelope != first_envelope
