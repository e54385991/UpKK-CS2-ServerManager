"""Coverage for the versioned ``/api/v1/servers`` create/read contract."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from modules.models.servers import ServerStatus


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _sample_server(**overrides):
    values = {
        "id": 7,
        "user_id": 1,
        "name": "bravo",
        "host": "10.0.0.8",
        "game_port": 27015,
        "status": ServerStatus.PENDING,
        "description": "fresh box",
        "default_map": "de_mirage",
        "max_players": 16,
        "ssh_port": 22,
        "ssh_user": "steam",
        "ssh_password": "should-never-leak",
        "sudo_password": "should-never-leak-sudo",
        "apt_mirror": "official",
        "game_directory": "/home/steam/cs2",
        "game_mode": "competitive",
        "game_type": "0",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "last_deployed": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(monkeypatch, *, admin: bool = False, users: list | None = None):
    app = create_app(lifespan=None)
    user = SimpleNamespace(
        id=1,
        username="owner",
        is_admin=admin,
        is_active=True,
        hashed_password="should-never-leak",
        s3_secret_access_key="should-never-leak-s3",
    )
    session = _database_session()
    owner_rows = users or []

    async def execute(*_args, **_kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: owner_rows))

    session.execute = execute

    async def fake_db():
        yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = fake_db
    return TestClient(app), user


def _create_payload(**overrides) -> dict:
    payload = {
        "name": "bravo",
        "host": "10.0.0.8",
        "ssh_user": "steam",
        "ssh_password": "s3cret-ssh",
        "sudo_password": "s3cret-sudo",
        "apt_mirror": "ustc",
        "captcha_token": "tok-1",
        "captcha_code": "AB12",
        "default_map": "de_mirage",
        "max_players": 16,
        "rcon_password": "rcon-secret",
        "steam_account_token": "GSLTsecret",
    }
    payload.update(overrides)
    return payload


def test_v1_create_server_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.post("/api/v1/servers", json=_create_payload())
    assert response.status_code == 401


def test_v1_create_server_requires_captcha_when_enabled(monkeypatch):
    client, _user = _client(monkeypatch)
    payload = _create_payload()
    del payload["captcha_token"]
    del payload["captcha_code"]
    response = client.post("/api/v1/servers", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "CAPTCHA is required"


def test_v1_create_server_returns_201_without_secrets(monkeypatch):
    client, _user = _client(monkeypatch)
    created = _sample_server()
    captured = {}

    async def fake_create(server_data, db, current_user, request):
        captured["password"] = server_data.ssh_password
        captured["captcha_token"] = server_data.captcha_token
        captured["captcha_code"] = server_data.captcha_code
        captured["rcon"] = server_data.rcon_password
        captured["sudo"] = server_data.sudo_password
        captured["apt_mirror"] = server_data.apt_mirror
        return created

    monkeypatch.setattr("api.routes.v1.servers.create_legacy_server", fake_create)

    response = client.post("/api/v1/servers", json=_create_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 7
    assert body["name"] == "bravo"
    assert body["host"] == "10.0.0.8"
    assert body["status"] == "pending"
    assert captured["password"] == "s3cret-ssh"
    assert captured["captcha_token"] == "tok-1"
    assert captured["captcha_code"] == "AB12"
    assert captured["rcon"] == "rcon-secret"
    assert captured["sudo"] == "s3cret-sudo"
    assert captured["apt_mirror"] == "ustc"
    assert body["apt_mirror"] == "official"
    assert body["has_sudo_password"] is True
    serialized = str(body)
    assert "s3cret-ssh" not in serialized
    assert "should-never-leak" not in serialized
    assert "rcon-secret" not in serialized
    assert "GSLTsecret" not in serialized
    assert "s3cret-sudo" not in serialized
    assert "should-never-leak-sudo" not in serialized
    assert "ssh_password" not in body
    assert "sudo_password" not in body
    assert "rcon_password" not in body
    assert "steam_account_token" not in body
    assert body["host_initialized"] is True
    assert body["missing_packages"] == []
    assert body["manual_install_command"] is None


def test_v1_create_server_reports_manual_install_when_init_fails(monkeypatch):
    from services.host_initialization import HostDependencyResult, attach_host_initialization

    client, _user = _client(monkeypatch)
    created = _sample_server()

    async def fake_create(*_args, **_kwargs):
        attach_host_initialization(
            created,
            HostDependencyResult(
                success=False,
                architecture_supported=True,
                architecture="amd64",
                missing_before=("lib32z1",),
                missing_after=("lib32z1",),
                installed=False,
                privilege="root",
                message=(
                    "Automatic package installation failed. Missing packages: lib32z1. "
                    "Install them on the host, then retry: sudo apt-get install -y lib32z1"
                ),
                manual_install_command="sudo apt-get install -y lib32z1",
                logs=(),
            ),
        )
        return created

    monkeypatch.setattr("api.routes.v1.servers.create_legacy_server", fake_create)
    response = client.post("/api/v1/servers", json=_create_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["host_initialized"] is False
    assert body["missing_packages"] == ["lib32z1"]
    assert body["manual_install_command"] == "sudo apt-get install -y lib32z1"
    assert "sudo apt-get install -y lib32z1" in body["initialization_message"]
    assert "s3cret-ssh" not in str(body)
    client, _user = _client(monkeypatch)

    async def fake_create(*_args, **_kwargs):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Invalid or expired CAPTCHA code")

    monkeypatch.setattr("api.routes.v1.servers.create_legacy_server", fake_create)
    response = client.post("/api/v1/servers", json=_create_payload())
    assert response.status_code == 400
    assert "CAPTCHA" in response.json()["detail"]


def test_v1_create_server_can_force_add_without_host_initialization(monkeypatch):
    client, _user = _client(monkeypatch)
    created = _sample_server()
    captured = {}

    async def fake_force_create(
        server_data, _db, _current_user, _request, *, skip_host_initialization
    ):
        captured["server_data"] = server_data
        captured["skip_host_initialization"] = skip_host_initialization
        return created

    monkeypatch.setattr("api.routes.v1.servers.create_server_record", fake_force_create)
    response = client.post(
        "/api/v1/servers",
        json=_create_payload(force_add=True),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["host_initialized"] is False
    assert "SteamCMD" in body["initialization_message"]
    assert captured["skip_host_initialization"] is True
    assert captured["server_data"].name == "bravo"
    assert not hasattr(captured["server_data"], "force_add")


def test_v1_get_server_includes_workspace_fields_without_secrets(monkeypatch):
    client, _user = _client(monkeypatch)

    async def fake_access(_db, server_id, _user):
        assert server_id == 7
        return _sample_server(
            server_name="bravo-in-game",
            session_manager="tmux",
            enable_panel_monitoring=True,
            monitor_interval_seconds=30,
            auto_restart_on_crash=False,
            is_ssh_down=True,
            a2s_query_host="query.example",
            a2s_query_port=27016,
            enable_a2s_monitoring=True,
            additional_parameters="+sv_hibernate_when_empty 0",
        )

    monkeypatch.setattr("api.routes.v1.servers.require_server_access", fake_access)
    response = client.get("/api/v1/servers/7")
    assert response.status_code == 200
    body = response.json()
    assert body["server_name"] == "bravo-in-game"
    assert body["enable_panel_monitoring"] is True
    assert body["monitor_interval_seconds"] == 30
    assert body["auto_restart_on_crash"] is False
    assert body["is_ssh_down"] is True
    assert body["a2s_query_host"] == "query.example"
    assert body["a2s_query_port"] == 27016
    assert body["enable_a2s_monitoring"] is True
    assert body["additional_parameters"] == "+sv_hibernate_when_empty 0"
    assert body["apt_mirror"] == "official"
    assert body["has_sudo_password"] is True
    assert body["ssh_pooled"] is False
    assert body["ssh_in_use"] is False
    assert body["ssh_active_leases"] == 0
    assert "ssh_password" not in body
    assert "sudo_password" not in body
    assert "should-never-leak" not in str(body)


def test_v1_patch_server_updates_and_hides_secrets(monkeypatch):
    client, _user = _client(monkeypatch)
    captured = {}

    async def fake_update(server_id, server_data, _db, _user, _request):
        captured["id"] = server_id
        captured["name"] = server_data.name
        captured["ssh_password"] = server_data.ssh_password
        captured["enable_panel_monitoring"] = server_data.enable_panel_monitoring
        updated = _sample_server(name=server_data.name or "bravo")
        updated.restart_required = True
        return updated

    monkeypatch.setattr("api.routes.v1.servers.update_legacy_server", fake_update)
    response = client.patch(
        "/api/v1/servers/7",
        json={
            "name": "bravo-renamed",
            "ssh_password": "new-secret",
            "enable_panel_monitoring": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert captured["id"] == 7
    assert captured["name"] == "bravo-renamed"
    assert captured["ssh_password"] == "new-secret"
    assert captured["enable_panel_monitoring"] is True
    assert body["name"] == "bravo-renamed"
    assert body["restart_required"] is True
    assert "ssh_password" not in body
    assert "sudo_password" not in body
    assert "new-secret" not in str(body)


def test_v1_patch_server_accepts_additional_parameters(monkeypatch):
    client, _user = _client(monkeypatch)
    captured = {}

    async def fake_update(_server_id, server_data, _db, _user, _request):
        captured["additional_parameters"] = server_data.additional_parameters
        updated = _sample_server(additional_parameters=server_data.additional_parameters)
        updated.restart_required = True
        return updated

    monkeypatch.setattr("api.routes.v1.servers.update_legacy_server", fake_update)
    response = client.patch(
        "/api/v1/servers/7",
        json={"additional_parameters": "+sv_hibernate_when_empty 0 +host_workshop_map 3171881962"},
    )
    assert response.status_code == 200
    assert (
        captured["additional_parameters"]
        == "+sv_hibernate_when_empty 0 +host_workshop_map 3171881962"
    )
    assert (
        response.json()["additional_parameters"]
        == "+sv_hibernate_when_empty 0 +host_workshop_map 3171881962"
    )
    assert response.json()["restart_required"] is True


def test_v1_patch_server_rejects_managed_additional_parameters(monkeypatch):
    client, _user = _client(monkeypatch)
    response = client.patch(
        "/api/v1/servers/7",
        json={"additional_parameters": "+map de_dust2"},
    )
    assert response.status_code == 422


def test_v1_create_server_accepts_tsinghua_alias(monkeypatch):
    client, _user = _client(monkeypatch)
    captured = {}

    async def fake_create(server_data, *_args, **_kwargs):
        captured["apt_mirror"] = server_data.apt_mirror
        captured["sudo"] = server_data.sudo_password
        return _sample_server(apt_mirror="tuna")

    monkeypatch.setattr("api.routes.v1.servers.create_legacy_server", fake_create)
    response = client.post(
        "/api/v1/servers",
        json=_create_payload(apt_mirror="tsinghua", sudo_password="s3cret-sudo"),
    )
    assert response.status_code == 201
    assert captured["apt_mirror"] == "tuna"
    assert captured["sudo"] == "s3cret-sudo"
    assert response.json()["apt_mirror"] == "tuna"
    assert "s3cret-sudo" not in str(response.json())


def test_v1_apply_apt_mirror_returns_202(monkeypatch):
    client, _user = _client(monkeypatch)
    server = _sample_server()
    captured = {}

    async def fake_access(_db, server_id, _user):
        assert server_id == 7
        return server

    async def fake_enqueue(*, server_id, mirror, actor_user_id):
        captured["server_id"] = server_id
        captured["mirror"] = mirror
        captured["actor"] = actor_user_id
        return {
            "operation_id": "op-mirror-1",
            "server_id": server_id,
            "action": "apply_apt_mirror",
            "status": "queued",
            "success": None,
            "message": None,
            "server_status": None,
            "started_at": datetime.now(timezone.utc),
            "completed_at": None,
            "actor_user_id": actor_user_id,
        }

    monkeypatch.setattr("api.routes.v1.servers.require_server_access", fake_access)
    monkeypatch.setattr("api.routes.v1.servers.enqueue_apply_apt_mirror", fake_enqueue)
    monkeypatch.setattr(
        "api.routes.v1.servers.redis_manager.get",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.servers.maintenance_lock_service.is_locked",
        AsyncMock(return_value=False),
    )

    response = client.post("/api/v1/servers/7/apt-mirror", json={"mirror": "tuna"})
    assert response.status_code == 202
    body = response.json()
    assert captured["mirror"] == "tuna"
    assert captured["server_id"] == 7
    assert server.apt_mirror == "tuna"
    assert body["action"] == "apply_apt_mirror"
    assert body["status"] == "queued"
    assert body["stream_url"] == "/api/v1/servers/7/operations/op-mirror-1/events"
    assert "password" not in str(body)


def test_v1_apply_apt_mirror_rejects_unknown_id(monkeypatch):
    client, _user = _client(monkeypatch)
    response = client.post("/api/v1/servers/7/apt-mirror", json={"mirror": "steamcmd"})
    assert response.status_code == 422


def test_v1_ssh_reconnect_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.post("/api/v1/servers/7/ssh-reconnect")
    assert response.status_code == 401


def test_v1_ssh_reconnect_success_without_secrets(monkeypatch):
    client, _user = _client(monkeypatch)

    async def fake_reconnect(server_id, _db, current_user):
        assert server_id == 7
        assert current_user.id == 1
        return {
            "success": True,
            "message": "SSH reconnected",
            "ssh_password": "should-never-leak",
        }

    monkeypatch.setattr("api.routes.v1.servers.reconnect_ssh_legacy", fake_reconnect)
    response = client.post("/api/v1/servers/7/ssh-reconnect")
    assert response.status_code == 200
    body = response.json()
    assert body == {"success": True, "message": "SSH reconnected"}
    assert "ssh_password" not in body
    assert "should-never-leak" not in str(body)


def test_v1_list_servers_returns_only_current_user_without_secrets(monkeypatch):
    client, _user = _client(monkeypatch)
    own = _sample_server(id=7, user_id=1, ssh_password="should-never-leak")
    other = _sample_server(id=9, user_id=2, name="foreign", ssh_password="other-secret")

    async def fake_mine(_db, user_id, skip, limit):
        assert user_id == 1
        return [own]

    async def fake_all(*_args, **_kwargs):
        return [own, other]

    monkeypatch.setattr("api.routes.v1.servers.Server.get_all_by_user", fake_mine)
    monkeypatch.setattr("api.routes.v1.servers.Server.get_all", fake_all)
    response = client.get("/api/v1/servers")
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [7]
    assert body[0]["owner_id"] is None
    assert body[0]["ssh_user"] == "steam"
    assert "ssh_password" not in body[0]
    assert "should-never-leak" not in str(body)
    assert "other-secret" not in str(body)


def test_v1_list_servers_scope_all_forbidden_for_non_admin(monkeypatch):
    client, _user = _client(monkeypatch)
    called = {"all": False}

    async def fake_all(*_args, **_kwargs):
        called["all"] = True
        return [_sample_server(id=9, user_id=2, name="foreign")]

    monkeypatch.setattr("api.routes.v1.servers.Server.get_all", fake_all)
    response = client.get("/api/v1/servers?scope=all")
    assert response.status_code == 403
    assert response.json()["detail"] == "Not enough permissions"
    assert called["all"] is False


def test_v1_list_servers_scope_all_admin_includes_owners_without_secrets(monkeypatch):
    owners = [
        SimpleNamespace(
            id=1,
            username="owner",
            is_admin=False,
            hashed_password="owner-hash",
            s3_secret_access_key="owner-s3",
        ),
        SimpleNamespace(
            id=2,
            username="alice",
            is_admin=True,
            hashed_password="alice-hash",
            s3_secret_access_key="alice-s3",
        ),
    ]
    client, _user = _client(monkeypatch, admin=True, users=owners)
    own = _sample_server(id=7, user_id=1, ssh_password="own-secret")
    foreign = _sample_server(
        id=9,
        user_id=2,
        name="alice-box",
        host="10.9.9.9",
        ssh_password="alice-secret",
    )

    async def fake_all(_db, skip, limit):
        return [own, foreign]

    async def fake_mine(*_args, **_kwargs):
        raise AssertionError("admin fleet must not fall back to get_all_by_user")

    monkeypatch.setattr("api.routes.v1.servers.Server.get_all", fake_all)
    monkeypatch.setattr("api.routes.v1.servers.Server.get_all_by_user", fake_mine)
    response = client.get("/api/v1/servers?scope=all")
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [7, 9]
    assert body[0]["owner_username"] == "owner"
    assert body[0]["owner_is_admin"] is False
    assert body[1]["owner_id"] == 2
    assert body[1]["owner_username"] == "alice"
    assert body[1]["owner_is_admin"] is True
    assert "use_panel_proxy" in body[0]
    assert "github_proxy" in body[0]
    assert "is_ssh_down" in body[0]
    assert body[0]["ssh_health_status"] == "unknown"
    assert body[0]["consecutive_ssh_failures"] == 0
    assert body[0]["ssh_health_failure_threshold"] == 84
    serialized = str(body)
    assert "own-secret" not in serialized
    assert "alice-secret" not in serialized
    assert "owner-hash" not in serialized
    assert "alice-s3" not in serialized
    assert "ssh_password" not in body[0]
    assert "hashed_password" not in body[1]


def test_v1_apply_system_defaults_returns_projection(monkeypatch):
    client, _user = _client(monkeypatch)

    async def fake_apply(server_id, _db, _user):
        assert server_id == 7
        return _sample_server(
            use_panel_proxy=True,
            github_proxy=None,
            ssh_health_status="healthy",
            consecutive_ssh_failures=0,
        )

    monkeypatch.setattr("api.routes.v1.servers.apply_defaults_legacy", fake_apply)
    response = client.post("/api/v1/servers/7/apply-system-defaults")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 7
    assert body["use_panel_proxy"] is True
    assert body["github_proxy"] is None
    assert "ssh_password" not in body


def test_v1_ssh_reconnect_reports_failure(monkeypatch):
    client, _user = _client(monkeypatch)

    async def fake_reconnect(*_args, **_kwargs):
        return {
            "success": False,
            "message": "Server marked as offline (SSH connection failed multiple times).",
        }

    monkeypatch.setattr("api.routes.v1.servers.reconnect_ssh_legacy", fake_reconnect)
    response = client.post("/api/v1/servers/7/ssh-reconnect")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "SSH connection failed" in body["message"]
    assert list(body.keys()) == ["success", "message"]


def test_v1_startup_command_is_masked(monkeypatch):
    client, _user = _client(monkeypatch)

    async def fake_startup(*_args, **_kwargs):
        return {
            "startup_command": 'tmux new -s cs2 -- bash -c "./cs2 +rcon_password ***RCON_PASSWORD***"',
            "cs2_command": "./cs2 +rcon_password ***RCON_PASSWORD***",
            "session_manager": "tmux",
            "game_mode_resolved": "competitive (game_type: 0, game_mode: 1)",
        }

    monkeypatch.setattr(
        "api.routes.v1.servers.get_startup_command_legacy",
        fake_startup,
    )
    response = client.get("/api/v1/servers/7/startup-command")
    assert response.status_code == 200
    body = response.json()
    assert body["session_manager"] == "tmux"
    assert "***RCON_PASSWORD***" in body["startup_command"]
    assert "should-never-leak" not in str(body)


def test_v1_delete_server_forwards_to_legacy(monkeypatch):
    client, _user = _client(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_delete(server_id, db, current_user, request):
        captured["server_id"] = server_id
        return None

    monkeypatch.setattr("api.routes.v1.servers.delete_legacy_server", fake_delete)
    response = client.delete("/api/v1/servers/7")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["server_id"] == 7


def test_v1_confirm_deployment(monkeypatch):
    client, _user = _client(monkeypatch)
    stamped = datetime.now(timezone.utc)

    async def fake_confirm(*_args, **_kwargs):
        return {
            "success": True,
            "message": "Deployment marked as complete",
            "status": "stopped",
            "last_deployed": stamped,
        }

    monkeypatch.setattr(
        "api.routes.v1.servers.confirm_deployment_legacy",
        fake_confirm,
    )
    response = client.post("/api/v1/servers/7/confirm-deployment")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "stopped"
    assert body["last_deployed"]
