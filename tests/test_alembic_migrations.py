"""Contract tests for Alembic adoption and credential expand migrations."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from cs2_manager.infrastructure import migrations
from cs2_manager.infrastructure.credentials import CredentialCipher, EncryptedText, hash_token

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_revision(filename: str, module_name: str):
    path = PROJECT_ROOT / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class _FakeConnection:
    def __init__(self, *, lock_result: int | None = 1, release_result: int | None = 1):
        self.dialect = SimpleNamespace(name="mysql")
        self.lock_result = lock_result
        self.release_result = release_result
        self.calls: list[str] = []
        self.transaction_active = False

    async def scalar(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append(sql)
        self.transaction_active = True
        if "DATABASE()" in sql:
            return "server_manager"
        if "GET_LOCK" in sql:
            return self.lock_result
        if "RELEASE_LOCK" in sql:
            return self.release_result
        raise AssertionError(sql)

    async def commit(self):
        self.calls.append("COMMIT")
        self.transaction_active = False

    async def rollback(self):
        self.calls.append("ROLLBACK")
        self.transaction_active = False

    def in_transaction(self):
        return self.transaction_active

    async def run_sync(self, function):
        self.calls.append(function.__name__)


class _FakeEngine:
    def __init__(self, connection: _FakeConnection):
        self.connection = connection

    def connect(self):
        return _AsyncContext(self.connection)


def test_revision_graph_has_one_expected_head():
    assert migrations.get_head_revisions() == ("0003_credential_data_backfill",)


def test_static_baseline_owns_every_managed_table(monkeypatch):
    revision = _load_revision("0001_legacy_baseline.py", "baseline_revision_for_test")
    created: set[str] = set()
    monkeypatch.setattr(
        revision.op, "create_table", lambda name, *args, **kwargs: created.add(name)
    )
    monkeypatch.setattr(revision.op, "create_index", lambda *args, **kwargs: None)

    revision.upgrade()

    assert created == set(migrations.MANAGED_TABLES)


def test_legacy_adoption_manifest_matches_every_static_baseline_column():
    source = (PROJECT_ROOT / "alembic" / "versions" / "0001_legacy_baseline.py").read_text()
    tree = ast.parse(source)
    actual: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            continue
        table_name = str(node.args[0].value)
        columns: set[str] = set()
        for argument in node.args[1:]:
            if isinstance(argument, ast.Starred):
                columns.update({"created_at", "updated_at"})
            elif (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Attribute)
                and argument.func.attr == "Column"
                and argument.args
                and isinstance(argument.args[0], ast.Constant)
            ):
                columns.add(str(argument.args[0].value))
        actual[table_name] = columns

    assert actual == {
        table_name: set(columns)
        for table_name, columns in migrations.REQUIRED_BASELINE_COLUMNS.items()
    }


def test_expand_manifest_matches_every_credential_shadow_model_column():
    # Importing modules.models registers the complete SQLModel metadata.
    from sqlmodel import SQLModel

    import modules.models  # noqa: F401

    revision = _load_revision(
        "0002_credential_security_expand.py",
        "credential_expand_revision_for_manifest_test",
    )
    model_shadow_columns = {
        (table.name, column.name)
        for table in SQLModel.metadata.tables.values()
        for column in table.columns
        if column.name.endswith("_encrypted")
    }
    migration_shadow_columns = {
        (table_name, f"{column_name}_encrypted")
        for table_name, column_names in revision.ENCRYPTED_COLUMNS.items()
        for column_name in column_names
    }
    data_revision = _load_revision(
        "0003_credential_data_backfill.py",
        "credential_data_revision_for_manifest_test",
    )
    data_columns = {
        (table_name, column_name)
        for table_name, column_names in data_revision.CREDENTIAL_COLUMNS.items()
        for column_name in column_names
    }

    assert migration_shadow_columns == model_shadow_columns
    assert data_columns == {
        (table_name, column_name.removesuffix("_encrypted"))
        for table_name, column_name in model_shadow_columns
    }
    assert not {
        (table.name, column.name)
        for table in SQLModel.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, EncryptedText)
    }


def test_expand_adds_security_fields_and_independent_shadows(monkeypatch):
    revision = _load_revision(
        "0002_credential_security_expand.py",
        "credential_expand_revision_for_operations_test",
    )
    column_state = {
        table: {
            name: {"name": name, "type": sa.String(length=255), "nullable": True} for name in names
        }
        for table, names in revision.ENCRYPTED_COLUMNS.items()
    }
    column_state["ssh_servers_sudo"]["sudo_password"]["nullable"] = False
    column_state["password_reset_tokens"] = {
        "token": {
            "name": "token",
            "type": sa.String(length=64),
            "nullable": False,
        }
    }
    column_state["scheduled_tasks"] = {
        "enabled": {"name": "enabled", "type": sa.Boolean(), "nullable": False},
        "next_run": {"name": "next_run", "type": sa.DateTime(), "nullable": True},
    }

    added: list[tuple[str, str]] = []
    altered: list[tuple[str, str, dict[str, object]]] = []
    indexes: list[tuple[str, str, tuple[str, ...], bool]] = []

    def record_added_column(table, column):
        added.append((table, column.name))
        column_state.setdefault(table, {})[column.name] = {
            "name": column.name,
            "type": column.type,
            "nullable": column.nullable,
        }

    monkeypatch.setattr(revision, "_columns", lambda table: column_state[table])
    monkeypatch.setattr(revision, "_index_names", lambda table: set())
    monkeypatch.setattr(
        revision.op,
        "add_column",
        record_added_column,
    )
    monkeypatch.setattr(
        revision.op,
        "alter_column",
        lambda table, column, **kwargs: altered.append((table, column, kwargs)),
    )
    monkeypatch.setattr(
        revision.op,
        "create_index",
        lambda name, table, columns, unique=False: indexes.append(
            (name, table, tuple(columns), unique)
        ),
    )

    revision.upgrade()

    shadow_columns = {
        (table, f"{column}_encrypted")
        for table, columns in revision.ENCRYPTED_COLUMNS.items()
        for column in columns
    }
    assert shadow_columns <= set(added)
    assert not {
        (table, column)
        for table, column, args in altered
        if (table, column)
        in {
            (name, credential)
            for name, credentials in revision.ENCRYPTED_COLUMNS.items()
            for credential in credentials
        }
        and "type_" in args
    }
    assert {
        ("servers", "credential_revision"),
        ("servers", "ssh_host_key_algorithm"),
        ("servers", "ssh_host_key_fingerprint"),
        ("servers", "api_key_hash"),
        ("users", "api_key_hash"),
        ("users", "api_key_prefix"),
        ("password_reset_tokens", "token_hash"),
        ("password_reset_tokens", "token_prefix"),
    } <= set(added)
    assert any(
        table == "password_reset_tokens" and column == "token" and args.get("nullable") is True
        for table, column, args in altered
    )
    assert (
        revision.SCHEDULER_INDEX,
        "scheduled_tasks",
        ("enabled", "next_run"),
        False,
    ) in indexes


def _backfill_database():
    metadata = sa.MetaData()
    sa.Table(
        "initialized_servers",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ssh_password", sa.Text()),
    )
    sa.Table(
        "servers",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("api_key", sa.Text()),
        sa.Column("api_key_hash", sa.CHAR(64)),
        sa.Column("ssh_password", sa.Text()),
        sa.Column("sudo_password", sa.Text()),
        sa.Column("server_password", sa.Text()),
        sa.Column("rcon_password", sa.Text()),
        sa.Column("steam_account_token", sa.Text()),
        sa.Column("discord_webhook_url", sa.Text()),
    )
    sa.Table(
        "ssh_servers_sudo",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sudo_password", sa.Text()),
    )
    sa.Table(
        "system_settings",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("global_github_token", sa.Text()),
        sa.Column("gmail_credentials_json", sa.Text()),
        sa.Column("gmail_token_json", sa.Text()),
        sa.Column("smtp_password", sa.Text()),
    )
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("api_key", sa.String(64)),
        sa.Column("api_key_hash", sa.CHAR(64)),
        sa.Column("api_key_prefix", sa.String(12)),
        sa.Column("steam_api_key", sa.Text()),
        sa.Column("github_token", sa.Text()),
        sa.Column("s3_access_key_id", sa.Text()),
        sa.Column("s3_secret_access_key", sa.Text()),
    )
    sa.Table(
        "password_reset_tokens",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(64)),
        sa.Column("token_hash", sa.CHAR(64)),
        sa.Column("token_prefix", sa.String(12)),
    )
    for table_name, column_names in {
        "initialized_servers": ("ssh_password",),
        "servers": (
            "api_key",
            "ssh_password",
            "sudo_password",
            "server_password",
            "rcon_password",
            "steam_account_token",
            "discord_webhook_url",
        ),
        "ssh_servers_sudo": ("sudo_password",),
        "system_settings": (
            "global_github_token",
            "gmail_credentials_json",
            "gmail_token_json",
            "smtp_password",
        ),
        "users": (
            "steam_api_key",
            "github_token",
            "s3_access_key_id",
            "s3_secret_access_key",
        ),
    }.items():
        table = metadata.tables[table_name]
        for column_name in column_names:
            table.append_column(sa.Column(f"{column_name}_encrypted", sa.Text()))
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            metadata.tables["initialized_servers"].insert(),
            {"id": 1, "ssh_password": "init-password"},
        )
        connection.execute(
            metadata.tables["servers"].insert(),
            {
                "id": 1,
                "api_key": "server-api-key",
                "ssh_password": "ssh-password",
                "sudo_password": "sudo-password",
                "server_password": "game-password",
                "rcon_password": "rcon-password",
                "steam_account_token": "gslt",
                "discord_webhook_url": "https://discord.invalid/hook",
            },
        )
        connection.execute(
            metadata.tables["ssh_servers_sudo"].insert(),
            {"id": 1, "sudo_password": "wizard-password"},
        )
        connection.execute(
            metadata.tables["system_settings"].insert(),
            {
                "id": 1,
                "global_github_token": "github-token",
                "gmail_credentials_json": '{"client":"secret"}',
                "gmail_token_json": '{"access":"token"}',
                "smtp_password": "smtp-password",
            },
        )
        connection.execute(
            metadata.tables["users"].insert(),
            {
                "id": 1,
                "api_key": "user-api-key",
                "steam_api_key": "steam-key",
                "github_token": "user-github-token",
                "s3_access_key_id": "access-key",
                "s3_secret_access_key": "secret-key",
            },
        )
        connection.execute(
            metadata.tables["password_reset_tokens"].insert(),
            {"id": 1, "token": "reset-token"},
        )
    return engine, metadata


def test_credential_data_backfill_encrypts_and_hashes_then_can_decrypt(monkeypatch):
    revision = _load_revision(
        "0003_credential_data_backfill.py",
        "credential_data_revision_for_round_trip_test",
    )
    engine, metadata = _backfill_database()
    cipher = CredentialCipher({"v1": bytes([7]) * 32}, "v1")
    token_key = "migration-token-hash-key"
    monkeypatch.setattr(revision, "_security_material", lambda: (cipher, token_key))

    with engine.begin() as connection:
        monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
        revision.upgrade()

    # The data phase is idempotent so an interrupted operator workflow can be
    # retried without double encryption or digest drift.
    with engine.begin() as connection:
        monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
        revision.upgrade()

    with engine.connect() as connection:
        server = connection.execute(sa.select(metadata.tables["servers"])).mappings().one()
        user = connection.execute(sa.select(metadata.tables["users"])).mappings().one()
        reset = (
            connection.execute(sa.select(metadata.tables["password_reset_tokens"])).mappings().one()
        )
        assert server["api_key"] == "server-api-key"
        assert str(server["api_key_encrypted"]).startswith("enc:v1:v1:")
        assert (
            cipher.decrypt(str(server["api_key_encrypted"]), aad="servers:1:api_key")
            == "server-api-key"
        )
        assert server["api_key_hash"] == hash_token("server-api-key", token_key)
        assert user["api_key_hash"] == hash_token("user-api-key", token_key)
        assert user["api_key_prefix"] == "user-api"
        assert reset["token_hash"] == hash_token("reset-token", token_key)
        assert reset["token_prefix"] == "reset-to"

    with engine.begin() as connection:
        monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
        revision.downgrade()

    with engine.connect() as connection:
        server = connection.execute(sa.select(metadata.tables["servers"])).mappings().one()
        assert server["api_key"] == "server-api-key"
        assert server["ssh_password"] == "ssh-password"
        assert server["api_key_encrypted"] is None
        assert server["ssh_password_encrypted"] is None
    engine.dispose()


def test_credential_data_validation_rejects_shadow_moved_to_another_record(monkeypatch):
    revision = _load_revision(
        "0003_credential_data_backfill.py",
        "credential_data_revision_for_row_aad_test",
    )
    engine, metadata = _backfill_database()
    cipher = CredentialCipher({"v1": bytes([21]) * 32}, "v1")
    monkeypatch.setattr(revision, "_security_material", lambda: (cipher, "hash-key"))

    with engine.begin() as connection:
        monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
        revision.upgrade()
        first_shadow = connection.scalar(
            sa.select(metadata.tables["servers"].c.api_key_encrypted).where(
                metadata.tables["servers"].c.id == 1
            )
        )
        connection.execute(
            metadata.tables["servers"].insert(),
            {
                "id": 2,
                "api_key": "second-server-key",
                "api_key_encrypted": first_shadow,
            },
        )
        with pytest.raises(RuntimeError, match="Credential shadow backfill incomplete"):
            revision._validate_upgrade(connection, cipher)
    engine.dispose()


def test_credential_data_downgrade_clears_shadows_without_decryption_key(monkeypatch):
    revision = _load_revision(
        "0003_credential_data_backfill.py",
        "credential_data_revision_for_keyless_downgrade_test",
    )
    engine, metadata = _backfill_database()
    cipher = CredentialCipher({"v1": bytes([22]) * 32}, "v1")
    monkeypatch.setattr(revision, "_security_material", lambda: (cipher, "hash-key"))

    with engine.begin() as connection:
        monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
        revision.upgrade()

    monkeypatch.setattr(
        revision,
        "_security_material",
        lambda: pytest.fail("downgrade must not require credential keys"),
    )
    with engine.begin() as connection:
        monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
        revision.downgrade()

    with engine.connect() as connection:
        server = connection.execute(sa.select(metadata.tables["servers"])).mappings().one()
        assert server["api_key"] == "server-api-key"
        assert server["api_key_encrypted"] is None
    engine.dispose()


def test_credential_data_backfill_rolls_back_all_updates_on_failure(monkeypatch):
    revision = _load_revision(
        "0003_credential_data_backfill.py",
        "credential_data_revision_for_rollback_test",
    )
    engine, metadata = _backfill_database()
    cipher = CredentialCipher({"v1": bytes([8]) * 32}, "v1")
    monkeypatch.setattr(revision, "_security_material", lambda: (cipher, "hash-key"))
    original_apply = revision._apply_batches

    def fail_after_first_batch(connection, batches):
        original_apply(connection, batches[:1])
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(revision, "_apply_batches", fail_after_first_batch)

    with pytest.raises(RuntimeError, match="simulated write failure"):
        with engine.begin() as connection:
            monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
            revision.upgrade()

    with engine.connect() as connection:
        initialized = (
            connection.execute(sa.select(metadata.tables["initialized_servers"])).mappings().one()
        )
        server = connection.execute(sa.select(metadata.tables["servers"])).mappings().one()
        assert initialized["ssh_password"] == "init-password"
        assert server["api_key_hash"] is None
    engine.dispose()


def test_credential_data_backfill_fails_closed_without_encryption_key(monkeypatch):
    revision = _load_revision(
        "0003_credential_data_backfill.py",
        "credential_data_revision_for_missing_key_test",
    )
    engine, metadata = _backfill_database()
    monkeypatch.setattr(
        revision,
        "_security_material",
        lambda: (CredentialCipher({}, ""), "hash-key"),
    )

    with pytest.raises(RuntimeError, match="Credential encryption is not configured"):
        with engine.begin() as connection:
            monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
            revision.upgrade()

    with engine.connect() as connection:
        initialized = (
            connection.execute(sa.select(metadata.tables["initialized_servers"])).mappings().one()
        )
        assert initialized["ssh_password"] == "init-password"
    engine.dispose()


def test_empty_credential_backfill_still_requires_encryption_key(monkeypatch):
    revision = _load_revision(
        "0003_credential_data_backfill.py",
        "credential_data_revision_for_empty_missing_key_test",
    )
    engine, metadata = _backfill_database()
    with engine.begin() as connection:
        for table in reversed(metadata.sorted_tables):
            connection.execute(table.delete())
    monkeypatch.setattr(
        revision,
        "_security_material",
        lambda: (CredentialCipher({}, ""), "hash-key"),
    )

    with pytest.raises(RuntimeError, match="Credential encryption is not configured"):
        with engine.begin() as connection:
            monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
            revision.upgrade()
    engine.dispose()


@pytest.mark.asyncio
async def test_mysql_lock_is_session_scoped_and_released_after_failure():
    connection = _FakeConnection()

    with pytest.raises(RuntimeError, match="migration body failed"):
        async with migrations._mysql_advisory_lock(  # noqa: SLF001
            connection,
            timeout_seconds=5,
        ):
            raise RuntimeError("migration body failed")

    assert any("GET_LOCK" in call for call in connection.calls)
    assert any("RELEASE_LOCK" in call for call in connection.calls)
    assert connection.calls.index(next(call for call in connection.calls if "GET_LOCK" in call)) < (
        connection.calls.index(next(call for call in connection.calls if "RELEASE_LOCK" in call))
    )


@pytest.mark.asyncio
async def test_mysql_lock_times_out_closed():
    connection = _FakeConnection(lock_result=0)

    with pytest.raises(migrations.MigrationLockTimeout):
        async with migrations._mysql_advisory_lock(  # noqa: SLF001
            connection,
            timeout_seconds=0,
        ):
            pytest.fail("lock body must not run")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tables", "revisions", "expect_legacy_adoption"),
    [
        (set(), (), False),
        ({"users", "servers"}, (), True),
        (
            {"users", "servers", "alembic_version"},
            ("0003_credential_data_backfill",),
            False,
        ),
    ],
    ids=("empty", "legacy-partial", "already-current"),
)
async def test_upgrade_paths_are_repeatable_and_legacy_only_runs_once(
    monkeypatch,
    tables,
    revisions,
    expect_legacy_adoption,
):
    connection = _FakeConnection()
    engine = _FakeEngine(connection)
    adopted: list[bool] = []

    async def fake_tables(_connection):
        return tables

    async def fake_revisions(_connection, _tables=None):
        return revisions

    async def fake_adopt(_connection, *, legacy_runner):
        del legacy_runner
        adopted.append(True)

    async def fake_status(_connection):
        return migrations.MigrationStatus(
            current_revisions=("0003_credential_data_backfill",),
            head_revisions=("0003_credential_data_backfill",),
            has_legacy_schema=False,
        )

    monkeypatch.setattr(migrations, "_table_names", fake_tables)
    monkeypatch.setattr(migrations, "_current_revisions", fake_revisions)
    monkeypatch.setattr(migrations, "_adopt_legacy_database", fake_adopt)
    monkeypatch.setattr(migrations, "_status_on_connection", fake_status)

    coordinator = migrations.MigrationCoordinator(engine)  # type: ignore[arg-type]
    status = await coordinator.upgrade()

    assert status.is_current
    assert bool(adopted) is expect_legacy_adoption
    assert "_upgrade_to_head" in connection.calls
    assert any("RELEASE_LOCK" in call for call in connection.calls)


@pytest.mark.asyncio
async def test_legacy_adoption_validates_before_stamping(monkeypatch):
    connection = _FakeConnection()
    runner_called = False

    async def legacy_runner(_connection):
        nonlocal runner_called
        runner_called = True

    async def incomplete_tables(_connection):
        return {"users", "servers"}

    monkeypatch.setattr(migrations, "_table_names", incomplete_tables)

    with pytest.raises(migrations.MigrationValidationError, match="required tables"):
        await migrations._adopt_legacy_database(  # noqa: SLF001
            connection,
            legacy_runner=legacy_runner,
        )

    assert runner_called
    assert "_stamp_baseline" not in connection.calls


@pytest.mark.asyncio
async def test_legacy_adoption_rejects_a_table_missing_any_baseline_column(monkeypatch):
    connection = _FakeConnection()

    async def legacy_runner(_connection):
        return None

    async def complete_tables(_connection):
        return set(migrations.MANAGED_TABLES)

    async def columns(_connection, table_name):
        required = set(migrations.REQUIRED_BASELINE_COLUMNS[table_name])
        if table_name == "users":
            required.remove("email")
        return required

    monkeypatch.setattr(migrations, "_table_names", complete_tables)
    monkeypatch.setattr(migrations, "_column_names", columns)

    with pytest.raises(migrations.MigrationValidationError, match="users.email"):
        await migrations._adopt_legacy_database(  # noqa: SLF001
            connection,
            legacy_runner=legacy_runner,
        )

    assert "_stamp_baseline" not in connection.calls


def test_lock_name_is_stable_bounded_and_database_specific():
    first = migrations._lock_name("production")  # noqa: SLF001
    second = migrations._lock_name("production")  # noqa: SLF001
    other = migrations._lock_name("staging")  # noqa: SLF001

    assert first == second
    assert first != other
    assert len(first) <= 64
