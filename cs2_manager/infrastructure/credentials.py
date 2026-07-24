"""Versioned credential encryption and token hashing primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import event, inspect, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import attributes
from sqlalchemy.types import Text, TypeDecorator

_CIPHERTEXT_PREFIX = "enc:v1:"
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _decode_key(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        key = base64.urlsafe_b64decode(value + padding)
    except ValueError as exc:
        raise ValueError("Credential encryption keys must be URL-safe base64") from exc
    if len(key) != 32:
        raise ValueError("Credential encryption keys must decode to exactly 32 bytes")
    return key


def _parse_keys(value: str | None) -> dict[str, bytes]:
    if not value or not value.strip():
        return {}
    raw = value.strip()
    if raw.startswith("{"):
        parsed: Any = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("CREDENTIAL_ENCRYPTION_KEYS JSON must be an object")
        entries = parsed.items()
    else:
        entries = (entry.split(":", 1) for entry in raw.split(",") if entry.strip())

    keys: dict[str, bytes] = {}
    for key_id, encoded in entries:
        key_id = str(key_id).strip()
        if not _KEY_ID_PATTERN.fullmatch(key_id):
            raise ValueError(f"Invalid credential key id: {key_id!r}")
        keys[key_id] = _decode_key(str(encoded).strip())
    return keys


class CredentialCipher:
    """Encrypt secrets with AES-256-GCM and explicit key versioning."""

    def __init__(self, keys: dict[str, bytes], active_key_id: str | None) -> None:
        self._keys = dict(keys)
        self.active_key_id = active_key_id or ""
        if self._keys and self.active_key_id not in self._keys:
            raise ValueError("CREDENTIAL_ACTIVE_KEY_ID must reference a configured key")

    @classmethod
    def from_settings(cls, settings: Any) -> "CredentialCipher":
        return cls(
            _parse_keys(getattr(settings, "CREDENTIAL_ENCRYPTION_KEYS", "")),
            getattr(settings, "CREDENTIAL_ACTIVE_KEY_ID", ""),
        )

    @property
    def enabled(self) -> bool:
        return bool(self._keys and self.active_key_id)

    @staticmethod
    def is_encrypted(value: str | None) -> bool:
        return bool(value and value.startswith(_CIPHERTEXT_PREFIX))

    def encrypt(self, value: str, *, aad: str) -> str:
        if self.is_encrypted(value):
            return value
        if not self.enabled:
            raise RuntimeError("Credential encryption is not configured")
        nonce = os.urandom(12)
        encrypted = AESGCM(self._keys[self.active_key_id]).encrypt(
            nonce,
            value.encode("utf-8"),
            aad.encode("utf-8"),
        )
        payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii").rstrip("=")
        return f"{_CIPHERTEXT_PREFIX}{self.active_key_id}:{payload}"

    def decrypt(self, value: str, *, aad: str) -> str:
        if not self.is_encrypted(value):
            return value
        try:
            _prefix, _version, key_id, payload = value.split(":", 3)
            key = self._keys[key_id]
            padding = "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload + padding)
            nonce, ciphertext = decoded[:12], decoded[12:]
            plaintext = AESGCM(key).decrypt(
                nonce,
                ciphertext,
                aad.encode("utf-8"),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError("Credential cannot be decrypted with the configured keys") from exc
        return plaintext.decode("utf-8")


@lru_cache(maxsize=1)
def configured_cipher() -> CredentialCipher:
    """Load the process cipher lazily to avoid configuration work during imports."""
    from modules.config import settings

    return CredentialCipher.from_settings(settings)


class EncryptedText(TypeDecorator[str]):
    """SQLAlchemy type which supports plaintext reads during the first migration release."""

    impl = Text
    cache_ok = True

    def __init__(self, aad: str) -> None:
        super().__init__()
        self.aad = aad

    def process_bind_param(self, value: str | None, _dialect) -> str | None:
        if value is None:
            return None
        cipher = configured_cipher()
        if cipher.is_encrypted(value):
            return value
        if not cipher.enabled:
            raise RuntimeError(
                "Credential encryption is not configured; refusing to store plaintext"
            )
        return cipher.encrypt(value, aad=self.aad)

    def process_result_value(self, value: str | None, _dialect) -> str | None:
        if value is None:
            return None
        cipher = configured_cipher()
        if not cipher.is_encrypted(value):
            return value
        return cipher.decrypt(value, aad=self.aad)


@dataclass(frozen=True, slots=True)
class CredentialShadow:
    """Map one legacy plaintext column to its first-release shadow column."""

    field: str
    encrypted_field: str


def credential_aad(table_name: str, record_id: object, field: str) -> str:
    """Build row-bound AES-GCM associated data for a credential envelope."""
    if record_id is None:
        raise ValueError("A persisted record id is required to encrypt a credential")
    return f"{table_name}:{record_id}:{field}"


def encrypt_credential_shadow(
    value: str | None,
    *,
    table_name: str,
    record_id: object,
    field: str,
    cipher: CredentialCipher | None = None,
) -> str | None:
    """Encrypt a shadow value with table, row, and field bound as AAD."""
    if value is None:
        return None
    active_cipher = cipher or configured_cipher()
    return active_cipher.encrypt(
        value,
        aad=credential_aad(table_name, record_id, field),
    )


def decrypt_credential_shadow(
    value: str | None,
    *,
    table_name: str,
    record_id: object,
    field: str,
    cipher: CredentialCipher | None = None,
) -> str | None:
    """Decrypt a row-bound shadow, rejecting plaintext in encrypted storage."""
    if value is None:
        return None
    active_cipher = cipher or configured_cipher()
    if not active_cipher.is_encrypted(value):
        raise ValueError(f"Credential shadow {table_name}.{field} is not encrypted")
    return active_cipher.decrypt(
        value,
        aad=credential_aad(table_name, record_id, field),
    )


def credential_shadow_update_values(
    *,
    table_name: str,
    record_id: object,
    values: Mapping[str, str | None],
) -> dict[str, str | None]:
    """Build explicit dual-write values for bulk DML which bypasses ORM events."""
    result: dict[str, str | None] = {}
    for field, value in values.items():
        result[field] = value
        result[f"{field}_encrypted"] = encrypt_credential_shadow(
            value,
            table_name=table_name,
            record_id=record_id,
            field=field,
        )
    return result


def _shadow_definitions(fields: Iterable[str]) -> tuple[CredentialShadow, ...]:
    return tuple(
        CredentialShadow(field=field, encrypted_field=f"{field}_encrypted") for field in fields
    )


def register_credential_shadows(model: type[Any], fields: Iterable[str]) -> None:
    """Install encrypted-first reads and plaintext/shadow dual-writes on a model.

    A SQLAlchemy ``TypeDecorator`` cannot include another column (the primary
    key) in bind processing. Mapper events are therefore used deliberately:
    explicit ids encrypt before INSERT, auto-increment ids encrypt in the same
    transaction immediately after INSERT, and ordinary ORM updates encrypt
    before UPDATE. Bulk SQL must use :func:`credential_shadow_update_values`.
    """
    definitions = _shadow_definitions(fields)
    table = model.__table__
    table_name = str(table.name)
    primary_keys = tuple(table.primary_key.columns)
    if len(primary_keys) != 1:
        raise TypeError(f"Credential shadow model {model.__name__} requires one primary key")
    primary_key = primary_keys[0]
    for definition in definitions:
        if definition.field not in table.columns:
            raise TypeError(f"Missing credential field {table_name}.{definition.field}")
        if definition.encrypted_field not in table.columns:
            raise TypeError(f"Missing credential shadow {table_name}.{definition.encrypted_field}")

    def record_id(target: Any) -> object:
        return getattr(target, primary_key.key)

    def hydrate(target: Any) -> None:
        persisted_id = record_id(target)
        for definition in definitions:
            # A partial refresh can leave a column unloaded. Avoid triggering a
            # nested lazy load from inside SQLAlchemy's refresh event.
            if definition.encrypted_field not in target.__dict__:
                continue
            encrypted = target.__dict__[definition.encrypted_field]
            if encrypted is None:
                continue
            plaintext = decrypt_credential_shadow(
                encrypted,
                table_name=table_name,
                record_id=persisted_id,
                field=definition.field,
            )
            attributes.set_committed_value(target, definition.field, plaintext)

    def encrypted_values(target: Any, *, changed_only: bool) -> dict[str, str | None]:
        persisted_id = record_id(target)
        state = inspect(target)
        values: dict[str, str | None] = {}
        for definition in definitions:
            if changed_only and not state.attrs[definition.field].history.has_changes():
                continue
            plaintext = getattr(target, definition.field)
            values[definition.encrypted_field] = encrypt_credential_shadow(
                plaintext,
                table_name=table_name,
                record_id=persisted_id,
                field=definition.field,
            )
        return values

    @event.listens_for(model, "load")
    def receive_load(target: Any, _context: Any) -> None:
        hydrate(target)

    @event.listens_for(model, "refresh")
    def receive_refresh(target: Any, _context: Any, _attrs: Any) -> None:
        hydrate(target)

    @event.listens_for(model, "before_insert")
    def receive_before_insert(_mapper: Any, _connection: Connection, target: Any) -> None:
        # MySQL auto-increment ids are not assigned until after INSERT.
        if record_id(target) is None:
            return
        for field, value in encrypted_values(target, changed_only=False).items():
            setattr(target, field, value)

    @event.listens_for(model, "after_insert")
    def receive_after_insert(_mapper: Any, connection: Connection, target: Any) -> None:
        values = encrypted_values(target, changed_only=False)
        if not values:
            return
        connection.execute(update(table).where(primary_key == record_id(target)).values(**values))
        for field, value in values.items():
            attributes.set_committed_value(target, field, value)

    @event.listens_for(model, "before_update")
    def receive_before_update(_mapper: Any, _connection: Connection, target: Any) -> None:
        for field, value in encrypted_values(target, changed_only=True).items():
            setattr(target, field, value)


def hash_token(token: str, key: str) -> str:
    """Return a stable HMAC-SHA256 digest suitable for indexed token lookup."""
    return hmac.new(key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


__all__ = [
    "CredentialCipher",
    "CredentialShadow",
    "EncryptedText",
    "configured_cipher",
    "credential_aad",
    "credential_shadow_update_values",
    "decrypt_credential_shadow",
    "encrypt_credential_shadow",
    "hash_token",
    "register_credential_shadows",
]
