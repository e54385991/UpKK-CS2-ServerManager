"""Alembic ownership, legacy adoption, and production revision checks."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
ALEMBIC_SCRIPT_PATH = PROJECT_ROOT / "alembic"
BASELINE_REVISION = "0001_legacy_baseline"
DEFAULT_LOCK_TIMEOUT_SECONDS = 60

MANAGED_TABLES = frozenset(
    {
        "custom_commands",
        "deployment_logs",
        "initialized_servers",
        "managed_plugins",
        "market_plugins",
        "monitoring_logs",
        "password_reset_tokens",
        "plugin_config_sources",
        "scheduled_tasks",
        "servers",
        "ssh_servers_sudo",
        "system_settings",
        "users",
    }
)

# Frozen alongside revision 0001. Legacy ``create_all`` can create a missing
# table but cannot repair missing columns on an existing table, so adoption
# must validate the complete baseline before stamping it as Alembic-owned.
REQUIRED_BASELINE_COLUMNS: dict[str, frozenset[str]] = {
    "custom_commands": frozenset(
        {"id", "user_id", "server_id", "name", "target", "commands", "created_at", "updated_at"}
    ),
    "deployment_logs": frozenset(
        {"id", "server_id", "action", "status", "output", "error_message", "created_at"}
    ),
    "initialized_servers": frozenset(
        {
            "id",
            "user_id",
            "name",
            "host",
            "ssh_port",
            "ssh_user",
            "ssh_password",
            "game_directory",
            "created_at",
            "updated_at",
        }
    ),
    "managed_plugins": frozenset(
        {
            "id",
            "server_id",
            "source_type",
            "source_key",
            "display_name",
            "repo_url",
            "market_plugin_id",
            "framework_key",
            "installed_release_id",
            "installed_version",
            "latest_version",
            "asset_glob",
            "custom_install_path",
            "exclude_dirs",
            "exclude_files",
            "auto_update_enabled",
            "backup_before_update",
            "restart_after_update",
            "last_check_at",
            "last_update_at",
            "last_status",
            "last_error",
            "created_at",
            "updated_at",
        }
    ),
    "market_plugins": frozenset(
        {
            "id",
            "github_url",
            "title",
            "description",
            "author",
            "version",
            "category",
            "tags",
            "is_recommended",
            "icon_url",
            "dependencies",
            "custom_install_path",
            "download_count",
            "install_count",
            "created_at",
            "updated_at",
        }
    ),
    "monitoring_logs": frozenset(
        {"id", "server_id", "event_type", "status", "message", "created_at"}
    ),
    "password_reset_tokens": frozenset(
        {"id", "user_id", "token", "expires_at", "used", "created_at"}
    ),
    "plugin_config_sources": frozenset(
        {
            "id",
            "server_id",
            "relative_path",
            "path_hash",
            "source_type",
            "is_default",
            "is_enabled",
            "created_at",
            "updated_at",
        }
    ),
    "scheduled_tasks": frozenset(
        {
            "id",
            "server_id",
            "name",
            "action",
            "enabled",
            "schedule_type",
            "schedule_value",
            "last_run",
            "next_run",
            "run_count",
            "last_status",
            "last_error",
            "created_at",
            "updated_at",
        }
    ),
    "servers": frozenset(
        {
            "id",
            "user_id",
            "name",
            "host",
            "ssh_port",
            "ssh_user",
            "auth_type",
            "ssh_password",
            "ssh_key_path",
            "sudo_password",
            "game_port",
            "game_directory",
            "status",
            "server_name",
            "server_password",
            "rcon_password",
            "steam_account_token",
            "default_map",
            "max_players",
            "game_mode",
            "game_type",
            "additional_parameters",
            "ip_address",
            "client_port",
            "tv_port",
            "tv_enable",
            "api_key",
            "backend_url",
            "auto_clear_crash_hours",
            "last_status_check",
            "enable_panel_monitoring",
            "monitor_interval_seconds",
            "auto_restart_on_crash",
            "a2s_query_host",
            "a2s_query_port",
            "enable_a2s_monitoring",
            "a2s_failure_threshold",
            "a2s_check_interval_seconds",
            "current_game_version",
            "enable_auto_update",
            "update_check_interval_hours",
            "last_update_check",
            "last_update_time",
            "enable_plugin_auto_update",
            "plugin_update_check_interval_hours",
            "last_plugin_update_check",
            "map_pool_sync_url",
            "cpu_affinity",
            "session_manager",
            "github_proxy",
            "use_panel_proxy",
            "discord_notifications_enabled",
            "discord_webhook_url",
            "discord_channel_name",
            "discord_notify_auto_updates",
            "discord_notify_manual_updates",
            "discord_notify_plugin_updates",
            "discord_notify_s3_backups",
            "discord_notify_crash_restarts",
            "discord_crash_restart_min_interval_minutes",
            "last_ssh_success",
            "last_ssh_failure",
            "consecutive_ssh_failures",
            "is_ssh_down",
            "enable_ssh_health_monitoring",
            "ssh_health_check_interval_hours",
            "ssh_health_failure_threshold",
            "last_ssh_health_check",
            "ssh_health_status",
            "description",
            "last_deployed",
            "created_at",
            "updated_at",
        }
    ),
    "ssh_servers_sudo": frozenset(
        {
            "id",
            "user_id",
            "host",
            "ssh_port",
            "sudo_user",
            "sudo_password",
            "created_at",
            "updated_at",
        }
    ),
    "system_settings": frozenset(
        {
            "id",
            "default_proxy_mode",
            "github_proxy_url",
            "global_github_token",
            "email_enabled",
            "email_provider",
            "email_from_address",
            "email_from_name",
            "gmail_credentials_json",
            "gmail_token_json",
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_use_tls",
            "created_at",
            "updated_at",
        }
    ),
    "users": frozenset(
        {
            "id",
            "username",
            "email",
            "hashed_password",
            "is_active",
            "is_admin",
            "api_key",
            "steam_api_key",
            "github_token",
            "s3_enabled",
            "s3_endpoint_url",
            "s3_region",
            "s3_bucket",
            "s3_access_key_id",
            "s3_secret_access_key",
            "s3_prefix",
            "s3_use_ssl",
            "s3_retention_count",
            "google_id",
            "oauth_provider",
            "created_at",
            "updated_at",
        }
    ),
}

LegacyRunner = Callable[[AsyncConnection], Awaitable[None]]


class MigrationError(RuntimeError):
    """Base error for migration coordination failures."""


class MigrationLockTimeout(MigrationError):
    """Raised when another process owns the database migration lock."""


class MigrationValidationError(MigrationError):
    """Raised when legacy normalization did not produce the baseline schema."""


class MigrationRequiredError(MigrationError):
    """Raised when application startup sees a database behind Alembic head."""


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    """Revision state suitable for readiness checks."""

    current_revisions: tuple[str, ...]
    head_revisions: tuple[str, ...]
    has_legacy_schema: bool

    @property
    def is_current(self) -> bool:
        return self.current_revisions == self.head_revisions


def _alembic_config(*, connection: Connection | None = None) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    # Resolve the location here as well as in alembic.ini so callers are not
    # sensitive to their current working directory.
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_PATH))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def get_head_revisions() -> tuple[str, ...]:
    """Return repository heads without opening a database connection."""
    script = ScriptDirectory.from_config(_alembic_config())
    return tuple(sorted(script.get_heads()))


async def _table_names(connection: AsyncConnection) -> set[str]:
    result = await connection.execute(
        text("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE()")
    )
    return {str(name) for name in result.scalars()}


async def _current_revisions(
    connection: AsyncConnection,
    table_names: set[str] | None = None,
) -> tuple[str, ...]:
    tables = table_names if table_names is not None else await _table_names(connection)
    if "alembic_version" not in tables:
        return ()
    result = await connection.execute(text("SELECT version_num FROM alembic_version"))
    return tuple(sorted(str(revision) for revision in result.scalars()))


async def _column_names(connection: AsyncConnection, table_name: str) -> set[str]:
    result = await connection.execute(
        text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name"
        ),
        {"table_name": table_name},
    )
    return {str(name) for name in result.scalars()}


def _lock_name(database_name: str) -> str:
    # MySQL lock names are limited to 64 characters. Hashing avoids truncation
    # collisions while keeping the prefix recognizable in performance_schema.
    digest = hashlib.sha256(database_name.encode("utf-8")).hexdigest()[:32]
    return f"cs2-manager:alembic:{digest}"


@asynccontextmanager
async def _mysql_advisory_lock(
    connection: AsyncConnection,
    *,
    timeout_seconds: int,
):
    if connection.dialect.name != "mysql":
        raise MigrationError(
            f"Production migrations require MySQL; got {connection.dialect.name!r}"
        )
    if not 0 <= timeout_seconds <= 3600:
        raise ValueError("timeout_seconds must be between 0 and 3600")

    database_name = await connection.scalar(text("SELECT DATABASE()"))
    if not database_name:
        raise MigrationError("The MySQL connection has no selected database")
    lock_name = _lock_name(str(database_name))
    acquired = await connection.scalar(
        text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
        {"lock_name": lock_name, "timeout_seconds": timeout_seconds},
    )
    # End SQLAlchemy's implicit transaction without releasing the session-level
    # MySQL lock.
    await connection.commit()
    if acquired != 1:
        raise MigrationLockTimeout(
            f"Timed out after {timeout_seconds}s waiting for database migration lock"
        )

    active_error: BaseException | None = None
    try:
        yield lock_name
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        try:
            if connection.in_transaction():
                await connection.rollback()
            released = await connection.scalar(
                text("SELECT RELEASE_LOCK(:lock_name)"),
                {"lock_name": lock_name},
            )
            await connection.commit()
            if released != 1 and active_error is None:
                raise MigrationError("MySQL migration lock was lost before release")
        except BaseException:
            if active_error is None:
                raise


def _stamp_baseline(sync_connection: Connection) -> None:
    command.stamp(
        _alembic_config(connection=sync_connection),
        BASELINE_REVISION,
    )


def _upgrade_to_head(sync_connection: Connection) -> None:
    command.upgrade(_alembic_config(connection=sync_connection), "head")


async def _default_legacy_runner(connection: AsyncConnection) -> None:
    # This import is deliberately isolated to first-run legacy adoption. It
    # must never be reached once alembic_version exists.
    from modules.migrations import run_migrations

    await run_migrations(connection)


async def _adopt_legacy_database(
    connection: AsyncConnection,
    *,
    legacy_runner: LegacyRunner,
) -> None:
    await legacy_runner(connection)
    if connection.in_transaction():
        await connection.commit()

    normalized_tables = await _table_names(connection)
    missing_tables = sorted(MANAGED_TABLES - normalized_tables)
    if missing_tables:
        raise MigrationValidationError(
            "Legacy normalization did not create required tables: " + ", ".join(missing_tables)
        )

    missing_columns: list[str] = []
    for table_name, required_columns in REQUIRED_BASELINE_COLUMNS.items():
        actual_columns = await _column_names(connection, table_name)
        missing_columns.extend(
            f"{table_name}.{column_name}"
            for column_name in sorted(required_columns - actual_columns)
        )
    if missing_columns:
        raise MigrationValidationError(
            "Legacy normalization did not create required columns: " + ", ".join(missing_columns)
        )
    await connection.run_sync(_stamp_baseline)


async def _status_on_connection(connection: AsyncConnection) -> MigrationStatus:
    tables = await _table_names(connection)
    current = await _current_revisions(connection, tables)
    return MigrationStatus(
        current_revisions=current,
        head_revisions=get_head_revisions(),
        has_legacy_schema=not current and bool(MANAGED_TABLES & tables),
    )


async def get_migration_status(engine: AsyncEngine) -> MigrationStatus:
    """Read database revision state without mutating it."""
    async with engine.connect() as connection:
        return await _status_on_connection(connection)


async def require_database_current(engine: AsyncEngine) -> MigrationStatus:
    """Fail startup/readiness when migrations have not been run separately."""
    status = await get_migration_status(engine)
    if not status.is_current:
        current = ",".join(status.current_revisions) or "unversioned"
        expected = ",".join(status.head_revisions)
        raise MigrationRequiredError(
            f"Database revision is {current}; expected {expected}. "
            "Run `uv run python -m cs2_manager.migrate upgrade` before startup."
        )
    return status


class MigrationCoordinator:
    """Serialize one-off schema upgrades and bridge pre-Alembic databases."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        lock_timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS,
        legacy_runner: LegacyRunner | None = None,
    ) -> None:
        self.engine = engine
        self.lock_timeout_seconds = lock_timeout_seconds
        self._legacy_runner = legacy_runner or _default_legacy_runner

    async def status(self) -> MigrationStatus:
        return await get_migration_status(self.engine)

    async def require_current(self) -> MigrationStatus:
        return await require_database_current(self.engine)

    async def upgrade(self) -> MigrationStatus:
        async with self.engine.connect() as connection:
            async with _mysql_advisory_lock(
                connection,
                timeout_seconds=self.lock_timeout_seconds,
            ):
                tables = await _table_names(connection)
                current = await _current_revisions(connection, tables)
                if not current and MANAGED_TABLES & tables:
                    await _adopt_legacy_database(
                        connection,
                        legacy_runner=self._legacy_runner,
                    )

                await connection.run_sync(_upgrade_to_head)
                if connection.in_transaction():
                    await connection.commit()
                status = await _status_on_connection(connection)
                if not status.is_current:
                    raise MigrationValidationError(
                        "Alembic upgrade completed without reaching repository head"
                    )
                return status

    @classmethod
    def from_url(
        cls,
        database_url: str,
        *,
        lock_timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> tuple[Self, AsyncEngine]:
        engine = create_async_engine(database_url, pool_pre_ping=True)
        return cls(engine, lock_timeout_seconds=lock_timeout_seconds), engine


async def migrate_database(
    database_url: str,
    *,
    lock_timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> MigrationStatus:
    """Create an owned engine, upgrade to head, and dispose every connection."""
    coordinator, engine = MigrationCoordinator.from_url(
        database_url,
        lock_timeout_seconds=lock_timeout_seconds,
    )
    try:
        return await coordinator.upgrade()
    finally:
        await engine.dispose()


__all__ = [
    "BASELINE_REVISION",
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "MANAGED_TABLES",
    "REQUIRED_BASELINE_COLUMNS",
    "MigrationCoordinator",
    "MigrationError",
    "MigrationLockTimeout",
    "MigrationRequiredError",
    "MigrationStatus",
    "MigrationValidationError",
    "get_head_revisions",
    "get_migration_status",
    "migrate_database",
    "require_database_current",
]
