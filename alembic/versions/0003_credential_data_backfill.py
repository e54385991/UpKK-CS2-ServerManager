"""Encrypt legacy credentials and backfill keyed token digests.

Revision ID: 0003_credential_data_backfill
Revises: 0002_credential_security_expand
Create Date: 2026-07-24
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from cryptography.exceptions import InvalidTag
from dotenv import load_dotenv

from alembic import context, op
from cs2_manager.infrastructure.credentials import (
    CredentialCipher,
    decrypt_credential_shadow,
    encrypt_credential_shadow,
    hash_token,
)

revision: str = "0003_credential_data_backfill"
down_revision: str | None = "0002_credential_security_expand"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CREDENTIAL_COLUMNS: dict[str, tuple[str, ...]] = {
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
}

# Contract-release boundary: only a later, separately deployed migration may
# make shadows non-null and remove legacy plaintext columns, after backup/
# restore rehearsal and a verified plaintext scan. This release never does so.


def _offline_mode() -> bool:
    try:
        return context.is_offline_mode()
    except NameError:
        return False


def _security_material() -> tuple[CredentialCipher, str]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    cipher = CredentialCipher.from_settings(
        SimpleNamespace(
            CREDENTIAL_ENCRYPTION_KEYS=os.getenv("CREDENTIAL_ENCRYPTION_KEYS", ""),
            CREDENTIAL_ACTIVE_KEY_ID=os.getenv("CREDENTIAL_ACTIVE_KEY_ID", ""),
        )
    )
    token_hash_key = os.getenv("TOKEN_HASH_KEY") or os.getenv("SECRET_KEY")
    if not token_hash_key:
        raise RuntimeError("TOKEN_HASH_KEY or SECRET_KEY is required for token backfill")
    return cipher, token_hash_key


def _decrypt_shadow(
    value: object,
    *,
    table_name: str,
    record_id: object,
    field: str,
    cipher: CredentialCipher,
) -> str:
    try:
        plaintext = decrypt_credential_shadow(
            str(value),
            table_name=table_name,
            record_id=record_id,
            field=field,
            cipher=cipher,
        )
    except (InvalidTag, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"Invalid credential shadow {table_name}.{field}[{record_id}]") from exc
    if plaintext is None:  # pragma: no cover - guarded by the non-null caller
        raise RuntimeError(f"Invalid credential shadow {table_name}.{field}[{record_id}]")
    return plaintext


def _credential_batches(
    bind: sa.Connection,
    cipher: CredentialCipher,
) -> list[tuple[sa.TextClause, list[dict[str, object]]]]:
    batches: list[tuple[sa.TextClause, list[dict[str, object]]]] = []
    for table_name, column_names in CREDENTIAL_COLUMNS.items():
        selected_columns = ", ".join(
            (
                "id",
                *column_names,
                *(f"{column_name}_encrypted" for column_name in column_names),
            )
        )
        rows = bind.execute(sa.text(f"SELECT {selected_columns} FROM {table_name}")).mappings()
        updates_by_column: dict[str, list[dict[str, object]]] = {
            column_name: [] for column_name in column_names
        }
        for row in rows:
            record_id = row["id"]
            for column_name in column_names:
                legacy_value = row[column_name]
                encrypted_column = f"{column_name}_encrypted"
                existing_shadow = row[encrypted_column]
                if existing_shadow is not None:
                    # Idempotent reruns preserve the original randomized
                    # envelope, but must prove it belongs to this exact row.
                    _decrypt_shadow(
                        existing_shadow,
                        table_name=table_name,
                        record_id=record_id,
                        field=column_name,
                        cipher=cipher,
                    )
                    continue
                if legacy_value is None:
                    continue
                transformed = encrypt_credential_shadow(
                    str(legacy_value),
                    table_name=table_name,
                    record_id=record_id,
                    field=column_name,
                    cipher=cipher,
                )
                updates_by_column[column_name].append(
                    {"record_id": record_id, "value": transformed}
                )

        for column_name, parameters in updates_by_column.items():
            if parameters:
                batches.append(
                    (
                        sa.text(
                            f"UPDATE {table_name} SET {column_name}_encrypted = :value "
                            "WHERE id = :record_id"
                        ),
                        parameters,
                    )
                )
    return batches


def _token_batches(
    bind: sa.Connection,
    cipher: CredentialCipher,
    token_hash_key: str,
) -> list[tuple[sa.TextClause, list[dict[str, object]]]]:
    batches: list[tuple[sa.TextClause, list[dict[str, object]]]] = []

    users: list[dict[str, object]] = []
    for row in bind.execute(
        sa.text("SELECT id, api_key FROM users WHERE api_key IS NOT NULL")
    ).mappings():
        token = str(row["api_key"])
        users.append(
            {
                "record_id": row["id"],
                "token_hash": hash_token(token, token_hash_key),
                "token_prefix": token[:8],
            }
        )
    if users:
        batches.append(
            (
                sa.text(
                    "UPDATE users SET api_key_hash = :token_hash, "
                    "api_key_prefix = :token_prefix WHERE id = :record_id"
                ),
                users,
            )
        )

    reset_tokens: list[dict[str, object]] = []
    for row in bind.execute(
        sa.text("SELECT id, token FROM password_reset_tokens WHERE token IS NOT NULL")
    ).mappings():
        token = str(row["token"])
        reset_tokens.append(
            {
                "record_id": row["id"],
                "token_hash": hash_token(token, token_hash_key),
                "token_prefix": token[:8],
            }
        )
    if reset_tokens:
        batches.append(
            (
                sa.text(
                    "UPDATE password_reset_tokens SET token_hash = :token_hash, "
                    "token_prefix = :token_prefix WHERE id = :record_id"
                ),
                reset_tokens,
            )
        )

    server_tokens: list[dict[str, object]] = []
    for row in bind.execute(
        sa.text(
            "SELECT id, api_key, api_key_encrypted FROM servers "
            "WHERE api_key IS NOT NULL OR api_key_encrypted IS NOT NULL"
        )
    ).mappings():
        encrypted = row["api_key_encrypted"]
        token = (
            _decrypt_shadow(
                encrypted,
                table_name="servers",
                record_id=row["id"],
                field="api_key",
                cipher=cipher,
            )
            if encrypted is not None
            else str(row["api_key"])
        )
        if token is None:
            continue
        server_tokens.append(
            {
                "record_id": row["id"],
                "token_hash": hash_token(token, token_hash_key),
            }
        )
    if server_tokens:
        batches.append(
            (
                sa.text("UPDATE servers SET api_key_hash = :token_hash WHERE id = :record_id"),
                server_tokens,
            )
        )

    return batches


def _apply_batches(
    bind: sa.Connection,
    batches: list[tuple[sa.TextClause, list[dict[str, object]]]],
) -> None:
    for statement, parameters in batches:
        bind.execute(statement, parameters)


def _validate_upgrade(bind: sa.Connection, cipher: CredentialCipher) -> None:
    invalid: list[str] = []
    for table_name, column_names in CREDENTIAL_COLUMNS.items():
        selected_columns = ", ".join(
            (
                "id",
                *column_names,
                *(f"{column_name}_encrypted" for column_name in column_names),
            )
        )
        rows = bind.execute(sa.text(f"SELECT {selected_columns} FROM {table_name}")).mappings()
        for row in rows:
            for column_name in column_names:
                legacy = row[column_name]
                encrypted = row[f"{column_name}_encrypted"]
                field_name = f"{table_name}.{column_name}[{row['id']}]"
                if legacy is not None and encrypted is None:
                    invalid.append(field_name)
                    continue
                if encrypted is None:
                    continue
                try:
                    plaintext = _decrypt_shadow(
                        encrypted,
                        table_name=table_name,
                        record_id=row["id"],
                        field=column_name,
                        cipher=cipher,
                    )
                except ValueError, RuntimeError:
                    invalid.append(field_name)
                    continue
                if legacy is not None and plaintext != str(legacy):
                    invalid.append(field_name)
    if invalid:
        raise RuntimeError("Credential shadow backfill incomplete: " + ", ".join(invalid))

    missing_hash_queries = {
        "users.api_key_hash": (
            "SELECT COUNT(*) FROM users WHERE api_key IS NOT NULL "
            "AND (api_key_hash IS NULL OR api_key_prefix IS NULL)"
        ),
        "password_reset_tokens.token_hash": (
            "SELECT COUNT(*) FROM password_reset_tokens WHERE token IS NOT NULL "
            "AND (token_hash IS NULL OR token_prefix IS NULL)"
        ),
        "servers.api_key_hash": (
            "SELECT COUNT(*) FROM servers WHERE api_key IS NOT NULL AND api_key_hash IS NULL"
        ),
    }
    missing_hashes = [
        field_name
        for field_name, query in missing_hash_queries.items()
        if bind.scalar(sa.text(query))
    ]
    if missing_hashes:
        raise RuntimeError("Token hash backfill incomplete: " + ", ".join(missing_hashes))


def upgrade() -> None:
    if _offline_mode():
        raise RuntimeError("Credential data backfill requires an online database connection")
    bind = op.get_bind()
    cipher, token_hash_key = _security_material()
    # This check is unconditional: an empty database has no row which would
    # otherwise reach encrypt(), and must not be stamped as safely migrated
    # without a usable keyring for the first credential written later.
    if not cipher.enabled:
        raise RuntimeError("Credential encryption is not configured")

    # Build every transformation before the first UPDATE. Invalid/missing keys
    # therefore cannot partially rewrite a database. Execution and Alembic's
    # version update share one transaction; any SQL or validation failure rolls
    # the complete data phase back and leaves this revision unapplied.
    credential_batches = _credential_batches(bind, cipher)
    token_batches = _token_batches(bind, cipher, token_hash_key)
    _apply_batches(bind, [*credential_batches, *token_batches])
    _validate_upgrade(bind, cipher)


def downgrade() -> None:
    if _offline_mode():
        raise RuntimeError("Credential data rollback requires an online database connection")
    bind = op.get_bind()
    # Legacy plaintext columns were never overwritten, so rollback needs no
    # decryption key and cannot corrupt the compatibility copy. Clear only the
    # first-release shadows; 0002 owns their eventual removal.
    for table_name, column_names in CREDENTIAL_COLUMNS.items():
        assignments = ", ".join(f"{column_name}_encrypted = NULL" for column_name in column_names)
        bind.execute(sa.text(f"UPDATE {table_name} SET {assignments}"))
