"""Expand credential storage and add the scheduler lookup index.

Revision ID: 0002_credential_security_expand
Revises: 0001_legacy_baseline
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0002_credential_security_expand"
down_revision: str | None = "0001_legacy_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEDULER_INDEX = "ix_scheduled_tasks_enabled_next_run"

# Complete first-release credential manifest. Legacy columns deliberately keep
# their original types and plaintext-compatible contents until a later contract
# release. Randomized AES-GCM envelopes live only in independent TEXT shadows.
ENCRYPTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "servers": (
        "api_key",
        "ssh_password",
        "sudo_password",
        "server_password",
        "rcon_password",
        "steam_account_token",
        "discord_webhook_url",
    ),
    "initialized_servers": ("ssh_password",),
    "users": (
        "steam_api_key",
        "github_token",
        "s3_access_key_id",
        "s3_secret_access_key",
    ),
    "ssh_servers_sudo": ("sudo_password",),
    "system_settings": (
        "global_github_token",
        "gmail_credentials_json",
        "gmail_token_json",
        "smtp_password",
    ),
}


def shadow_column(column_name: str) -> str:
    return f"{column_name}_encrypted"


def _offline_mode() -> bool:
    try:
        return context.is_offline_mode()
    except NameError:
        # Revision unit tests invoke upgrade() outside EnvironmentContext.
        return False


def _columns(table_name: str) -> dict[str, dict[str, object]]:
    return {
        str(column["name"]): column for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _index_names(table_name: str) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name") is not None
    }


def _ensure_column(table_name: str, column: sa.Column) -> None:
    if _offline_mode() or column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _ensure_index(
    name: str,
    table_name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    if _offline_mode() or name not in _index_names(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def _ensure_hash_type(table_name: str, column_name: str) -> None:
    if _offline_mode():
        return
    column = _columns(table_name).get(column_name)
    if column is None:
        raise RuntimeError(f"Cannot normalize token digest: {table_name}.{column_name} is missing")
    existing_type = column["type"]
    if isinstance(existing_type, sa.CHAR) and existing_type.length == 64:
        return
    op.alter_column(
        table_name,
        column_name,
        existing_type=existing_type,
        type_=sa.CHAR(length=64),
        existing_nullable=bool(column["nullable"]),
    )


def _add_credential_shadows() -> None:
    for table_name, column_names in ENCRYPTED_COLUMNS.items():
        for column_name in column_names:
            if not _offline_mode() and column_name not in _columns(table_name):
                raise RuntimeError(
                    f"Cannot expand credentials: {table_name}.{column_name} is missing"
                )
            _ensure_column(
                table_name,
                sa.Column(shadow_column(column_name), sa.Text(), nullable=True),
            )


def _drop_credential_shadows() -> None:
    for table_name, column_names in ENCRYPTED_COLUMNS.items():
        existing = None if _offline_mode() else _columns(table_name)
        for column_name in column_names:
            encrypted_column = shadow_column(column_name)
            if existing is None or encrypted_column in existing:
                # The plaintext-compatible legacy column is the rollback source
                # of truth. Clear randomized envelopes before removing storage.
                op.execute(f"UPDATE {table_name} SET {encrypted_column} = NULL")
                op.drop_column(table_name, encrypted_column)


def upgrade() -> None:
    _add_credential_shadows()

    _ensure_column(
        "servers",
        sa.Column(
            "credential_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    _ensure_column(
        "servers",
        sa.Column("ssh_host_key_algorithm", sa.String(length=64), nullable=True),
    )
    _ensure_column(
        "servers",
        sa.Column("ssh_host_key_fingerprint", sa.String(length=128), nullable=True),
    )
    _ensure_column(
        "servers",
        sa.Column("api_key_hash", sa.CHAR(length=64), nullable=True),
    )
    _ensure_hash_type("servers", "api_key_hash")
    _ensure_index(
        "ix_servers_api_key_hash",
        "servers",
        ["api_key_hash"],
        unique=True,
    )

    _ensure_column(
        "users",
        sa.Column("api_key_hash", sa.CHAR(length=64), nullable=True),
    )
    _ensure_hash_type("users", "api_key_hash")
    _ensure_column(
        "users",
        sa.Column("api_key_prefix", sa.String(length=12), nullable=True),
    )
    _ensure_index(
        "ix_users_api_key_hash",
        "users",
        ["api_key_hash"],
        unique=True,
    )

    token_column = (
        {"type": sa.String(length=64), "nullable": False}
        if _offline_mode()
        else _columns("password_reset_tokens").get("token")
    )
    if token_column is None:
        raise RuntimeError("Cannot expand reset tokens: legacy token column is missing")
    if not bool(token_column["nullable"]):
        op.alter_column(
            "password_reset_tokens",
            "token",
            existing_type=token_column["type"],
            existing_nullable=False,
            nullable=True,
        )
    _ensure_column(
        "password_reset_tokens",
        sa.Column("token_hash", sa.CHAR(length=64), nullable=True),
    )
    _ensure_hash_type("password_reset_tokens", "token_hash")
    _ensure_column(
        "password_reset_tokens",
        sa.Column("token_prefix", sa.String(length=12), nullable=True),
    )
    _ensure_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )

    _ensure_index(
        SCHEDULER_INDEX,
        "scheduled_tasks",
        ["enabled", "next_run"],
    )


def downgrade() -> None:
    if _offline_mode():
        _drop_credential_shadows()
        op.drop_index(SCHEDULER_INDEX, table_name="scheduled_tasks")
        op.drop_index(
            "ix_password_reset_tokens_token_hash",
            table_name="password_reset_tokens",
        )
        op.drop_column("password_reset_tokens", "token_prefix")
        op.drop_column("password_reset_tokens", "token_hash")
        op.execute("DELETE FROM password_reset_tokens WHERE token IS NULL")
        op.alter_column(
            "password_reset_tokens",
            "token",
            existing_type=sa.String(length=64),
            existing_nullable=True,
            nullable=False,
        )
        op.drop_index("ix_users_api_key_hash", table_name="users")
        op.drop_column("users", "api_key_prefix")
        op.drop_column("users", "api_key_hash")
        op.drop_column("servers", "ssh_host_key_fingerprint")
        op.drop_column("servers", "ssh_host_key_algorithm")
        op.drop_column("servers", "credential_revision")
        op.drop_index("ix_servers_api_key_hash", table_name="servers")
        op.drop_column("servers", "api_key_hash")
        return

    _drop_credential_shadows()

    if SCHEDULER_INDEX in _index_names("scheduled_tasks"):
        op.drop_index(SCHEDULER_INDEX, table_name="scheduled_tasks")

    if "ix_password_reset_tokens_token_hash" in _index_names("password_reset_tokens"):
        op.drop_index(
            "ix_password_reset_tokens_token_hash",
            table_name="password_reset_tokens",
        )
    reset_columns = _columns("password_reset_tokens")
    if "token_prefix" in reset_columns:
        op.drop_column("password_reset_tokens", "token_prefix")
    if "token_hash" in reset_columns:
        op.drop_column("password_reset_tokens", "token_hash")
    # New tokens deliberately have no plaintext equivalent. They are invalid
    # after rolling back and can safely be reissued.
    op.execute("DELETE FROM password_reset_tokens WHERE token IS NULL")
    token_column = _columns("password_reset_tokens").get("token")
    if token_column is not None and bool(token_column["nullable"]):
        op.alter_column(
            "password_reset_tokens",
            "token",
            existing_type=token_column["type"],
            existing_nullable=True,
            nullable=False,
        )

    if "ix_users_api_key_hash" in _index_names("users"):
        op.drop_index("ix_users_api_key_hash", table_name="users")
    user_columns = _columns("users")
    if "api_key_prefix" in user_columns:
        op.drop_column("users", "api_key_prefix")
    if "api_key_hash" in user_columns:
        op.drop_column("users", "api_key_hash")

    server_columns = _columns("servers")
    if "ix_servers_api_key_hash" in _index_names("servers"):
        op.drop_index("ix_servers_api_key_hash", table_name="servers")
    if "api_key_hash" in server_columns:
        op.drop_column("servers", "api_key_hash")
    for column_name in (
        "ssh_host_key_fingerprint",
        "ssh_host_key_algorithm",
        "credential_revision",
    ):
        if column_name in server_columns:
            op.drop_column("servers", column_name)
