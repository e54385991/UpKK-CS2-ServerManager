"""PostgreSQL-only schema and Alembic migration contracts."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

import modules.models  # noqa: F401
from alembic import command
from modules.config import settings
from modules.database_migrations import (
    MIGRATION_ADVISORY_LOCK_KEY,
    DatabaseMigrationError,
    _acquire_migration_lock,
    _current_heads,
    _server_version_num,
    _upgrade,
    alembic_config,
    code_heads,
    database_status,
    upgrade_database,
)
from modules.models import AuthType, Server, User

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_postgresql_models_and_static_baseline_are_the_schema_authority():
    assert len(SQLModel.metadata.tables) == 30
    assert code_heads() == ("0010_user_steamcmd_max_retries",)
    assert "create_all" not in (PROJECT_ROOT / "modules/database.py").read_text()

    revision = PROJECT_ROOT / "alembic/versions/0001_postgresql_baseline.py"
    source = revision.read_text()
    assert "modules.models" not in source
    assert "sqlmodel" not in source.casefold()


def test_revision_ids_longer_than_alembic_default_widen_version_num():
    long_ids: list[str] = []
    for path in sorted((PROJECT_ROOT / "alembic/versions").glob("*.py")):
        source = path.read_text()
        marker = 'revision: str = "'
        start = source.index(marker) + len(marker)
        revision = source[start : source.index('"', start)]
        if len(revision) > 32:
            long_ids.append(revision)
            assert "ALTER COLUMN version_num TYPE VARCHAR(128)" in source
    assert long_ids == ["0007_discord_server_administrators"]
    assert len("0007_discord_server_administrators") > 32


def test_models_use_jsonb_nonnative_enums_and_expected_query_indexes():
    json_columns = []
    enum_columns = []
    index_names = set()
    for table in SQLModel.metadata.sorted_tables:
        index_names.update(index.name for index in table.indexes)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                json_columns.append(f"{table.name}.{column.name}")
            if isinstance(column.type, SQLAlchemyEnum):
                enum_columns.append(column)

    assert json_columns
    assert enum_columns
    assert all(column.type.native_enum is False for column in enum_columns)
    assert {
        "ix_deployment_logs_server_created",
        "ix_scheduled_tasks_due",
        "ix_ai_conversations_user_updated",
        "ix_ai_messages_conversation_id_id",
        "ix_ai_tool_runs_run_created",
        "ix_custom_commands_server_user_created",
        "ix_initialized_servers_user_created",
        "ix_audit_logs_category_created",
        "ix_audit_logs_actor_created",
        "ix_audit_logs_created_at",
        "uq_users_username_ci",
        "uq_users_email_ci",
    } <= index_names


def test_database_url_handles_password_characters_without_string_concatenation():
    configured = settings.model_copy(
        update={
            "POSTGRES_USER": "app user",
            "POSTGRES_PASSWORD": "p@ss:/?#[]",
            "POSTGRES_HOST": "db.internal",
            "POSTGRES_DATABASE": "cs2 manager",
        }
    )

    assert configured.database_url.drivername == "postgresql+psycopg"
    assert configured.database_url.username == "app user"
    assert configured.database_url.password == "p@ss:/?#[]"
    assert configured.database_url.database == "cs2 manager"


class _VersionConnection:
    def __init__(self, value: object):
        self.value = value

    async def scalar(self, _statement, _parameters=None):
        return self.value


@pytest.mark.asyncio
async def test_postgresql_17_is_rejected_before_schema_access():
    with pytest.raises(DatabaseMigrationError, match=r"PostgreSQL 18\+"):
        await _server_version_num(_VersionConnection("170006"))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_nonpositive_migration_lock_timeout_is_rejected():
    with pytest.raises(DatabaseMigrationError, match="at least 1"):
        await _acquire_migration_lock(_VersionConnection(True), 0)  # type: ignore[arg-type]


class _FakeMigrationConnection:
    def __init__(self, *, heads: tuple[str, ...], acquire: bool = True):
        self.heads = heads
        self.acquire = acquire
        self.commits = 0
        self.rollbacks = 0
        self.upgraded = False
        self._in_txn = False

    def in_transaction(self) -> bool:
        return self._in_txn

    async def scalar(self, statement, parameters=None):
        sql = str(statement)
        self._in_txn = True
        if "server_version_num" in sql:
            return 180000
        if "pg_try_advisory_lock" in sql:
            return self.acquire
        if "pg_advisory_unlock" in sql:
            return True
        raise AssertionError(sql)

    async def commit(self) -> None:
        self.commits += 1
        self._in_txn = False

    async def rollback(self) -> None:
        self.rollbacks += 1
        self._in_txn = False

    async def execute(self, statement, parameters=None):
        sql = str(statement)
        self._in_txn = True
        if "lock_timeout" in sql:
            return None
        raise AssertionError(sql)

    async def run_sync(self, fn):
        self._in_txn = True
        if fn is _current_heads:
            return self.heads
        if fn is _upgrade:
            self.upgraded = True
            self.heads = code_heads()
            return None
        raise AssertionError(fn)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None and self._in_txn:
            await self.commit()
        elif exc_type is not None and self._in_txn:
            await self.rollback()
        return False


class _FakeMigrationEngine:
    def __init__(self, lock: _FakeMigrationConnection, work: _FakeMigrationConnection):
        self._lock = lock
        self._work = work
        self.connects = 0
        self.begin_count = 0

    def connect(self):
        self.connects += 1
        if self.connects == 1:
            return self._lock
        return self._work

    def begin(self):
        self.begin_count += 1
        return self._work


def test_upgrade_database_keeps_lock_and_schema_work_on_separate_connections():
    source = (PROJECT_ROOT / "modules/database_migrations.py").read_text()
    assert "lock_connection" in source
    assert "_apply_schema_upgrade" in source
    assert "engine.begin()" in source
    assert "SET LOCAL lock_timeout" in source
    assert source.index("await _read_current_heads") < source.index("run_sync(_upgrade)")
    env_source = (PROJECT_ROOT / "alembic/env.py").read_text()
    assert 'config.attributes.get("connection") is None' in env_source
    for revision in (
        "0006_discord_channel_managers.py",
        "0007_discord_server_administrators.py",
    ):
        revision_source = (PROJECT_ROOT / "alembic/versions" / revision).read_text()
        assert "IF NOT EXISTS" in revision_source
        assert "_ensure_boolean_column" in revision_source
    seventh = (PROJECT_ROOT / "alembic/versions/0007_discord_server_administrators.py").read_text()
    upgrade_body = seventh.split("def upgrade() -> None:", 1)[1]
    assert upgrade_body.index("_widen_alembic_version_num()") < upgrade_body.index(
        "_ensure_boolean_column("
    )


@pytest.mark.asyncio
async def test_upgrade_database_skips_alembic_when_already_at_head():
    expected = code_heads()
    lock = _FakeMigrationConnection(heads=expected)
    work = _FakeMigrationConnection(heads=expected)
    engine = _FakeMigrationEngine(lock, work)
    status = await upgrade_database(engine, lock_timeout_seconds=5)  # type: ignore[arg-type]
    assert status.is_current is True
    assert work.upgraded is False
    assert engine.begin_count == 0
    assert lock.rollbacks == 0


@pytest.mark.asyncio
async def test_upgrade_database_commits_schema_work_before_releasing_lock():
    lock = _FakeMigrationConnection(heads=("0005_discord_global_binding",))
    work = _FakeMigrationConnection(heads=("0005_discord_global_binding",))
    engine = _FakeMigrationEngine(lock, work)
    status = await upgrade_database(engine, lock_timeout_seconds=5)  # type: ignore[arg-type]
    assert work.upgraded is True
    assert engine.begin_count == 1
    assert work.commits >= 1
    assert status.is_current is True
    assert status.current_heads == code_heads()


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 with a disposable PostgreSQL 18+ server",
)
@pytest.mark.asyncio
async def test_postgresql_18_empty_upgrade_concurrency_crud_and_drift_check():
    """Exercise a disposable database on the configured PostgreSQL 18+ cluster."""
    database_name = f"cs2_manager_test_{uuid.uuid4().hex}"
    admin_url = settings.database_url.set(database="postgres")
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    test_engine = create_async_engine(settings.database_url.set(database=database_name))

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))

        first, second = await asyncio.gather(
            upgrade_database(test_engine, lock_timeout_seconds=30),
            upgrade_database(test_engine, lock_timeout_seconds=30),
        )
        assert first.is_current is True
        assert second.is_current is True
        assert (await database_status(test_engine)).is_current is True

        async with test_engine.connect() as lock_holder:
            await lock_holder.scalar(
                text("SELECT pg_advisory_lock(:key)"),
                {"key": MIGRATION_ADVISORY_LOCK_KEY},
            )
            await lock_holder.commit()
            with pytest.raises(DatabaseMigrationError, match="timed out after 1s"):
                await upgrade_database(test_engine, lock_timeout_seconds=1)
            assert (
                await lock_holder.scalar(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": MIGRATION_ADVISORY_LOCK_KEY},
                )
                is True
            )
            await lock_holder.commit()

        async with test_engine.connect() as connection:
            application_table_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
                )
            )
            native_enum_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_type "
                    "WHERE typtype = 'e' AND typnamespace = 'public'::regnamespace"
                )
            )
            jsonb_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND data_type = 'jsonb'"
                )
            )
            assert application_table_count == 30
            assert native_enum_count == 0
            assert jsonb_count and jsonb_count > 0

            def check_no_drift(sync_connection):
                command.check(alembic_config(connection=sync_connection))

            await connection.run_sync(check_no_drift)

        async with AsyncSession(test_engine, expire_on_commit=False) as session:
            user = User(
                username="CaseUser",
                email="User@Example.com",
                hashed_password="hash",
                api_key="CaseSensitiveToken",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            assert user.id == 1
            assert await User.get_by_username(session, "caseuser") == user
            assert await User.get_by_email(session, "USER@example.COM") == user

            server = Server(
                user_id=user.id,
                name="PostgreSQL JSONB",
                host="127.0.0.1",
                ssh_user="steam",
                auth_type=AuthType.PASSWORD,
                plugin_post_update_command_ids=[2, 5],
            )
            session.add(server)
            await session.commit()
            await session.refresh(server)
            server_id = server.id
            result = await session.scalar(
                select(Server).where(Server.plugin_post_update_command_ids.contains([5]))
            )
            assert result == server

            session.add(
                User(
                    username="caseuser",
                    email="different@example.com",
                    hashed_password="hash",
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            session.add(
                User(
                    username="another-user",
                    email="another@example.com",
                    hashed_password="hash",
                    api_key="casesensitivetoken",
                )
            )
            await session.commit()
            assert await session.scalar(select(func.count()).select_from(User)) == 2

            with pytest.raises(IntegrityError):
                await session.execute(
                    text("UPDATE servers SET status = 'BROKEN' WHERE id = :id"),
                    {"id": server_id},
                )
                await session.commit()
            await session.rollback()
    finally:
        await test_engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        await admin_engine.dispose()
