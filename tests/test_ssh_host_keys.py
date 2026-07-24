"""SSH trust-on-explicit-confirmation and strict pinning coverage."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from api.routes.servers import crud
from modules.models import AuthType, Server
from modules.schemas import ServerCreate
from services.ssh_connection_pool import SSHConnectionPool
from services.ssh_host_keys import (
    HostKeyConfirmationRequired,
    SSHHostKeyIdentity,
    pinned_host_key_options,
    scan_ssh_host_key,
)


class _Key:
    def __init__(self, algorithm="ssh-ed25519", fingerprint="SHA256:trusted") -> None:
        self.algorithm = algorithm
        self.fingerprint = fingerprint

    def get_algorithm(self):
        return self.algorithm

    def get_fingerprint(self, hash_name="sha256"):
        assert hash_name == "sha256"
        return self.fingerprint


@pytest.mark.asyncio
async def test_scan_reads_host_key_without_authentication(monkeypatch):
    get_key = AsyncMock(return_value=_Key())
    monkeypatch.setattr("services.ssh_host_keys.asyncssh.get_server_host_key", get_key)

    identity = await scan_ssh_host_key("game.example", 2222)

    assert identity == SSHHostKeyIdentity("ssh-ed25519", "SHA256:trusted")
    get_key.assert_awaited_once_with("game.example", 2222, config=None)


def test_pinned_client_accepts_only_exact_algorithm_and_fingerprint():
    options = pinned_host_key_options("ssh-ed25519", "SHA256:trusted")
    validator = options["client_factory"]()

    assert options["known_hosts"] == b""
    assert options["server_host_key_algs"] == ["ssh-ed25519"]
    assert validator.validate_host_public_key("host", "addr", 22, _Key()) is True
    assert (
        validator.validate_host_public_key("host", "addr", 22, _Key(fingerprint="SHA256:attacker"))
        is False
    )
    assert (
        validator.validate_host_public_key("host", "addr", 22, _Key(algorithm="rsa-sha2-512"))
        is False
    )


@pytest.mark.asyncio
async def test_pool_fails_closed_for_unconfirmed_legacy_server():
    pool = SSHConnectionPool()
    server = Server(
        id=3,
        user_id=7,
        name="legacy",
        host="game.example",
        ssh_user="cs2",
        ssh_password="secret",
        auth_type=AuthType.PASSWORD,
    )

    with pytest.raises(HostKeyConfirmationRequired):
        await pool._open_connection(server)


class _CreateDatabase:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_create_requires_confirmation_before_sending_password(monkeypatch):
    monkeypatch.setattr(Server, "get_by_name_and_user", AsyncMock(return_value=None))
    monkeypatch.setattr(
        Server,
        "get_by_host_directory_and_user",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        crud,
        "scan_ssh_host_key",
        AsyncMock(return_value=SSHHostKeyIdentity("ssh-ed25519", "SHA256:trusted")),
    )
    validate_captcha = AsyncMock(return_value=True)
    monkeypatch.setattr(crud.captcha_service, "validate_captcha", validate_captcha)
    connect = AsyncMock()
    monkeypatch.setattr(crud.asyncssh, "connect", connect)

    request = ServerCreate(
        name="new",
        host="game.example",
        ssh_user="cs2",
        ssh_password="never-sent",
        captcha_token="captcha-token",
        captcha_code="ABCD",
    )

    with pytest.raises(HTTPException) as exc_info:
        await crud.create_server(
            request,
            Response(),
            _CreateDatabase(),
            SimpleNamespace(id=7),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "ssh_host_key_confirmation_required"
    connect.assert_not_awaited()
    validate_captcha.assert_not_awaited()
