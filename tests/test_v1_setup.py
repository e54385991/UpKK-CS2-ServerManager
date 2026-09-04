"""Coverage for the versioned ``/api/v1/setup`` host auto-setup contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client():
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def test_v1_setup_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    assert client.get("/api/v1/setup/initialized-servers").status_code == 401
    assert client.get("/api/v1/setup/manual-script").status_code == 401
    assert (
        client.post(
            "/api/v1/setup/auto-setup",
            json={
                "name": "box",
                "host": "10.0.0.8",
                "ssh_user": "root",
                "ssh_password": "secret",
                "captcha_token": "tok",
                "captcha_code": "ABCD",
            },
        ).status_code
        == 401
    )


def test_v1_setup_lists_initialized_hosts_without_passwords(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.setup.redis_manager.get_initialized_servers",
        AsyncMock(
            return_value=[
                {
                    "key": "init:1:abc",
                    "name": "lan",
                    "host": "192.168.50.143",
                    "ssh_port": 22,
                    "ssh_user": "cs2server",
                    "ssh_password": "should-never-leak",
                    "game_directory": "/home/cs2server/cs2",
                    "created_at": 1.5,
                }
            ]
        ),
    )
    response = client.get("/api/v1/setup/initialized-servers")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["key"] == "init:1:abc"
    assert body[0]["host"] == "192.168.50.143"
    assert "ssh_password" not in body[0]


def test_v1_setup_credentials_are_owner_only(monkeypatch):
    client, _user = _client()
    monkeypatch.setattr(
        "api.routes.v1.setup.redis_manager.get_initialized_server",
        AsyncMock(
            return_value={
                "key": "init:2:stolen",
                "user_id": 99,
                "name": "other",
                "host": "10.0.0.9",
                "ssh_port": 22,
                "ssh_user": "cs2server",
                "ssh_password": "secret",
                "game_directory": "/home/cs2server/cs2",
                "created_at": 2.0,
            }
        ),
    )
    response = client.get("/api/v1/setup/initialized-servers/init:2:stolen/credentials")
    assert response.status_code == 403


def test_v1_setup_auto_setup_wraps_legacy_path(monkeypatch):
    client, _user = _client()

    async def fake_auto_setup(setup_req, current_user, db):
        assert setup_req.host == "10.0.0.8"
        assert setup_req.ssh_password == "secret"
        assert current_user.id == 1
        return SimpleNamespace(
            success=True,
            message="ok",
            cs2_username="cs2server",
            cs2_password="generated",
            game_directory="/home/cs2server/cs2",
            logs=["connected"],
            initialized_server_id="init:1:abc",
        )

    monkeypatch.setattr("api.routes.v1.setup.auto_setup_server", fake_auto_setup)
    response = client.post(
        "/api/v1/setup/auto-setup",
        json={
            "name": "box",
            "host": "10.0.0.8",
            "ssh_user": "root",
            "ssh_password": "secret",
            "captcha_token": "tok",
            "captcha_code": "ABCD",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["cs2_username"] == "cs2server"
    assert body["cs2_password"] == "generated"
    assert body["logs"] == ["connected"]


def test_v1_setup_auto_setup_propagates_captcha_failure(monkeypatch):
    client, _user = _client()

    async def reject(*_args, **_kwargs):
        raise HTTPException(status_code=400, detail="Invalid or expired CAPTCHA code")

    monkeypatch.setattr("api.routes.v1.setup.auto_setup_server", reject)
    response = client.post(
        "/api/v1/setup/auto-setup",
        json={
            "name": "box",
            "host": "10.0.0.8",
            "ssh_user": "root",
            "ssh_password": "secret",
            "captcha_token": "tok",
            "captcha_code": "XXXX",
        },
    )
    assert response.status_code == 400


def test_v1_setup_manual_script_includes_runtime_guards():
    client, _user = _client()
    response = client.get("/api/v1/setup/manual-script", params={"cs2_username": "cs2server"})
    assert response.status_code == 200
    body = response.json()
    script = body["script"]
    assert body["cs2_username"] == "cs2server"
    assert body["password"]
    assert "DPkg::Lock::Timeout=120" in script
    assert "Acquire::Retries=3" in script
    assert "amd64|x86_64) ;;" in script
    assert "libc6-i386 lib32gcc-s1 lib32stdc++6 lib32z1" in script
    assert "Required dependency verification failed" in script
    assert "cs2server:" in script


def test_v1_setup_manual_script_rejects_bad_username():
    client, _user = _client()
    response = client.get("/api/v1/setup/manual-script", params={"cs2_username": "Root User"})
    assert response.status_code == 422
