"""Tests for portable server configuration bundles."""

import pytest
from pydantic import ValidationError

from modules.models import AuthType, Server
from modules.schemas.servers import ServerConfigEntry, ServerConfigExport
from services.server_config_transfer import (
    SECRET_SERVER_FIELDS,
    config_entry_values,
    server_to_config_entry,
)


def _server() -> Server:
    return Server(
        id=41,
        user_id=9,
        name="Production",
        host="192.0.2.10",
        ssh_user="cs2server",
        auth_type=AuthType.PASSWORD,
        ssh_password="ssh-secret",
        sudo_password="sudo-secret",
        server_password="server-secret",
        rcon_password="rcon-secret",
        steam_account_token="STEAMTOKEN",
        discord_webhook_url="https://discord.example/secret",
        status="running",
        api_key="panel-api-key",
    )


def test_redacted_export_removes_secrets_and_runtime_identity():
    entry = server_to_config_entry(_server(), include_secrets=False)

    assert set(entry.redacted_fields) == SECRET_SERVER_FIELDS
    assert all(getattr(entry, field) is None for field in SECRET_SERVER_FIELDS)
    assert entry.name == "Production"
    assert entry.host == "192.0.2.10"
    assert "id" not in entry.model_dump()
    assert "api_key" not in entry.model_dump()
    assert "status" not in entry.model_dump()


def test_full_export_preserves_secrets_and_bundle_round_trips():
    entry = server_to_config_entry(_server(), include_secrets=True)
    bundle = ServerConfigExport(servers=[entry], include_secrets=True)
    restored = ServerConfigExport.model_validate(bundle.model_dump(mode="json"))

    restored_entry = restored.servers[0]
    assert restored_entry.ssh_password == "ssh-secret"
    assert restored_entry.rcon_password == "rcon-secret"
    assert restored_entry.discord_webhook_url == "https://discord.example/secret"
    assert restored_entry.redacted_fields == []


def test_redacted_fields_are_omitted_for_update_but_present_for_create():
    entry = server_to_config_entry(_server(), include_secrets=False)

    update_values = config_entry_values(entry, preserve_redacted=True)
    create_values = config_entry_values(entry)

    assert SECRET_SERVER_FIELDS.isdisjoint(update_values)
    assert SECRET_SERVER_FIELDS <= create_values.keys()
    assert create_values["ssh_password"] is None


def test_import_rejects_unknown_redacted_fields():
    with pytest.raises(ValidationError):
        ServerConfigEntry(
            name="Invalid",
            host="192.0.2.20",
            ssh_user="cs2server",
            redacted_fields=["api_key"],
        )
