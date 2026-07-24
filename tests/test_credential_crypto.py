"""Credential encryption and token hashing contracts."""

import base64

import pytest
import sqlalchemy as sa
from cryptography.exceptions import InvalidTag
from sqlmodel import Session, create_engine

from cs2_manager.infrastructure import credentials
from cs2_manager.infrastructure.credentials import (
    CredentialCipher,
    EncryptedText,
    credential_shadow_update_values,
    decrypt_credential_shadow,
    hash_token,
)
from modules.models import User


def _key(byte: int) -> bytes:
    return bytes([byte]) * 32


def test_credential_cipher_round_trip_and_key_rotation():
    cipher = CredentialCipher({"v1": _key(1), "v2": _key(2)}, "v2")
    encrypted = cipher.encrypt("top-secret", aad="servers:42:ssh_password")

    assert encrypted.startswith("enc:v1:v2:")
    assert "top-secret" not in encrypted
    assert cipher.decrypt(encrypted, aad="servers:42:ssh_password") == "top-secret"

    rotated = CredentialCipher({"v1": _key(1), "v2": _key(2), "v3": _key(3)}, "v3")
    assert rotated.decrypt(encrypted, aad="servers:42:ssh_password") == "top-secret"


def test_credential_cipher_authenticates_context():
    cipher = CredentialCipher({"v1": _key(1)}, "v1")
    encrypted = cipher.encrypt("top-secret", aad="users:7:github_token")

    with pytest.raises(InvalidTag):
        cipher.decrypt(encrypted, aad="users:8:github_token")


def test_plaintext_is_supported_during_expand_migrate_contract_release():
    cipher = CredentialCipher({"v1": _key(1)}, "v1")
    assert cipher.decrypt("legacy-plaintext", aad="users:7:github_token") == "legacy-plaintext"


def test_encrypted_type_refuses_new_plaintext_without_a_keyring(monkeypatch):
    monkeypatch.setattr(
        credentials,
        "configured_cipher",
        lambda: CredentialCipher({}, ""),
    )

    with pytest.raises(RuntimeError, match="refusing to store plaintext"):
        EncryptedText("users.github_token").process_bind_param("secret", None)


def test_settings_parser_requires_32_byte_keys():
    encoded = base64.urlsafe_b64encode(b"short").decode()
    settings = type(
        "Settings",
        (),
        {
            "CREDENTIAL_ENCRYPTION_KEYS": f'{{"v1":"{encoded}"}}',
            "CREDENTIAL_ACTIVE_KEY_ID": "v1",
        },
    )()
    with pytest.raises(ValueError, match="32 bytes"):
        CredentialCipher.from_settings(settings)


def test_token_hash_is_stable_and_keyed():
    assert hash_token("token", "key-a") == hash_token("token", "key-a")
    assert hash_token("token", "key-a") != hash_token("token", "key-b")


def test_bulk_shadow_values_dual_write_and_bind_ciphertext_to_row(monkeypatch):
    cipher = CredentialCipher({"v1": _key(3)}, "v1")
    monkeypatch.setattr(credentials, "configured_cipher", lambda: cipher)

    values = credential_shadow_update_values(
        table_name="system_settings",
        record_id=11,
        values={"gmail_token_json": '{"token":"secret"}'},
    )

    assert values["gmail_token_json"] == '{"token":"secret"}'
    shadow = values["gmail_token_json_encrypted"]
    assert isinstance(shadow, str)
    assert (
        decrypt_credential_shadow(
            shadow,
            table_name="system_settings",
            record_id=11,
            field="gmail_token_json",
            cipher=cipher,
        )
        == '{"token":"secret"}'
    )
    with pytest.raises(InvalidTag):
        decrypt_credential_shadow(
            shadow,
            table_name="system_settings",
            record_id=12,
            field="gmail_token_json",
            cipher=cipher,
        )


def test_orm_shadow_dual_write_encrypted_first_and_plaintext_fallback(monkeypatch):
    cipher = CredentialCipher({"v1": _key(4)}, "v1")
    monkeypatch.setattr(credentials, "configured_cipher", lambda: cipher)
    engine = create_engine("sqlite://")
    User.__table__.create(engine)

    with Session(engine, expire_on_commit=False) as session:
        user = User(
            username="shadow-user",
            email="shadow@example.invalid",
            hashed_password="unused",
            github_token="first-secret",
        )
        session.add(user)
        session.flush()
        assert user.id is not None
        record_id = user.id

        stored = (
            session.connection()
            .execute(
                sa.select(
                    User.__table__.c.github_token,
                    User.__table__.c.github_token_encrypted,
                ).where(User.__table__.c.id == record_id)
            )
            .one()
        )
        assert stored.github_token == "first-secret"
        assert stored.github_token_encrypted != "first-secret"
        assert (
            cipher.decrypt(
                stored.github_token_encrypted,
                aad=f"users:{record_id}:github_token",
            )
            == "first-secret"
        )

        user.github_token = "replacement-secret"
        session.flush()
        replacement_shadow = session.connection().scalar(
            sa.select(User.__table__.c.github_token_encrypted).where(
                User.__table__.c.id == record_id
            )
        )
        assert isinstance(replacement_shadow, str)
        assert (
            cipher.decrypt(replacement_shadow, aad=f"users:{record_id}:github_token")
            == "replacement-secret"
        )

        # Encrypted-first reads ignore a stale compatibility copy.
        session.connection().execute(
            User.__table__.update()
            .where(User.__table__.c.id == record_id)
            .values(github_token="stale-legacy")
        )
        session.expire(user)
        assert user.github_token == "replacement-secret"

        # Rows not yet backfilled retain the first-release plaintext fallback.
        session.connection().execute(
            User.__table__.update()
            .where(User.__table__.c.id == record_id)
            .values(github_token="legacy-only", github_token_encrypted=None)
        )
        session.expire(user)
        assert user.github_token == "legacy-only"
    engine.dispose()


def test_orm_rejects_shadow_ciphertext_swapped_between_records(monkeypatch):
    cipher = CredentialCipher({"v1": _key(5)}, "v1")
    monkeypatch.setattr(credentials, "configured_cipher", lambda: cipher)
    engine = create_engine("sqlite://")
    User.__table__.create(engine)

    with Session(engine, expire_on_commit=False) as session:
        first = User(
            username="first",
            email="first@example.invalid",
            hashed_password="unused",
            github_token="first-secret",
        )
        second = User(
            username="second",
            email="second@example.invalid",
            hashed_password="unused",
            github_token="second-secret",
        )
        session.add_all((first, second))
        session.commit()
        assert first.id is not None and second.id is not None

        first_shadow = session.connection().scalar(
            sa.select(User.__table__.c.github_token_encrypted).where(
                User.__table__.c.id == first.id
            )
        )
        session.connection().execute(
            User.__table__.update()
            .where(User.__table__.c.id == second.id)
            .values(github_token_encrypted=first_shadow)
        )
        session.expire(second)
        with pytest.raises(InvalidTag):
            _ = second.github_token
    engine.dispose()
