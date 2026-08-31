"""Coverage for the versioned game-mode catalog, preflight, and 202 install."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from modules.models.servers import ServerStatus


def _database_session():
    return SimpleNamespace(
        add=lambda *_a, **_k: None,
        commit=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(),
    )


def _sample_server(**overrides):
    values = {
        "id": 1,
        "name": "alpha",
        "host": "10.0.0.1",
        "game_port": 27015,
        "status": ServerStatus.STOPPED,
        "user_id": 1,
        "game_directory": "/home/cs2server/cs2kz",
        "additional_parameters": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _sample_plan(**overrides) -> dict:
    plan = {
        "server_id": 1,
        "mode_id": "kz",
        "wipe_addons": False,
        "addons_path": "/home/cs2server/cs2kz/cs2/game/csgo/addons",
        "current": {"css": False, "mapchooser": False},
        "startup": {
            "before": None,
            "after": "+sv_hibernate_when_empty 0 +host_workshop_map 3082213334 -timeout 120",
            "changed": True,
        },
        "plugin_config": {
            "UseGameTimeLimit": False,
            "EnforceTimeLimit": True,
            "ChangeMapUse_host_workshop_map": True,
        },
        "maps": [{"name": "kz_variety", "workshop_id": "3250132197"}],
        "wait_files": [
            "addons/counterstrikesharp/configs/plugins/MapChooser/config.json",
        ],
        "plugin_plans": {},
        "hard_conflicts": [],
        "warnings": [],
        "steps": [
            {
                "id": "startup",
                "action": "upsert_launch_args",
                "status": "pending",
            }
        ],
        "mutations": [
            {
                "id": "wipe_addons",
                "target": "/home/cs2server/cs2kz/cs2/game/csgo/addons",
                "before": "existing addons tree",
                "after": "empty addons directory",
                "destructive": True,
                "status": "pending",
            }
        ],
        "blocked": False,
        "blocking_reasons": [],
        "plan_hash": "a" * 64,
    }
    plan.update(overrides)
    return plan


def _client(*, monkeypatch):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)

    async def override_db():
        yield _database_session()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        "api.routes.v1.game_modes.require_server_access",
        AsyncMock(return_value=_sample_server()),
    )
    return TestClient(app), user


def test_v1_game_modes_require_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/servers/1/game-modes")
    assert response.status_code == 401


def test_v1_game_modes_catalog(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.game_modes.catalog_for_server",
        AsyncMock(
            return_value={
                "server_id": 1,
                "reachable": True,
                "additional_parameters": None,
                "addons_path": "/home/cs2server/cs2kz/cs2/game/csgo/addons",
                "addons_present": True,
                "swiftly_installed": False,
                "modes": [
                    {
                        "id": "kz",
                        "launch_upsert": {"-timeout": "120"},
                        "frameworks": ["counterstrikesharp"],
                        "market_plugin_titles": ["cs2kz-metamod"],
                        "maps": [{"name": "kz_variety", "workshop_id": "3250132197"}],
                        "plugin_config": {"UseGameTimeLimit": False},
                        "startup_workshop_map": "3082213334",
                        "present": {"counterstrikesharp": False},
                        "missing_market_plugins": [],
                    }
                ],
            }
        ),
    )
    response = client.get("/api/v1/servers/1/game-modes")
    assert response.status_code == 200
    body = response.json()
    assert body["modes"][0]["id"] == "kz"
    assert body["addons_path"].endswith("/cs2/game/csgo/addons")


def test_v1_game_mode_preflight_includes_wipe_mutation(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    planner = AsyncMock(return_value=_sample_plan(wipe_addons=True))
    monkeypatch.setattr("api.routes.v1.game_modes.build_game_mode_plan", planner)
    response = client.post(
        "/api/v1/servers/1/game-modes/kz/preflight",
        json={"wipe_addons": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["wipe_addons"] is True
    assert body["mutations"][0]["destructive"] is True
    assert planner.await_args.kwargs["wipe_addons"] is True


def test_v1_game_mode_install_requires_wipe_acknowledgement(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    response = client.post(
        "/api/v1/servers/1/game-modes/kz/install",
        json={"wipe_addons": True, "plan_hash": "a" * 64},
    )
    assert response.status_code == 422


def test_v1_game_mode_install_rejects_stale_hash(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.game_modes.reject_stuck_lock_unless_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.game_modes.build_game_mode_plan",
        AsyncMock(return_value=_sample_plan()),
    )
    response = client.post(
        "/api/v1/servers/1/game-modes/kz/install",
        json={"plan_hash": "b" * 64},
    )
    assert response.status_code == 409


def test_v1_game_mode_install_returns_202(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.game_modes.reject_stuck_lock_unless_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.game_modes.build_game_mode_plan",
        AsyncMock(return_value=_sample_plan()),
    )
    enqueue = AsyncMock(
        return_value={
            "operation_id": operation_id,
            "server_id": 1,
            "action": "install_game_mode",
            "status": "queued",
            "success": None,
            "message": None,
            "server_status": None,
            "actor_user_id": user.id,
            "started_at": "2026-08-31T00:00:00+00:00",
            "completed_at": None,
            "command": "game-mode install kz --wipe-addons",
        }
    )
    monkeypatch.setattr("api.routes.v1.game_modes.enqueue_game_mode_install", enqueue)
    response = client.post(
        "/api/v1/servers/1/game-modes/kz/install",
        json={
            "wipe_addons": True,
            "wipe_addons_acknowledged": True,
            "plan_hash": "a" * 64,
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["action"] == "install_game_mode"
    assert body["operation_id"] == operation_id
    assert enqueue.await_args.kwargs["wipe_addons"] is True
    assert enqueue.await_args.kwargs["mode_id"] == "kz"


def test_v1_unknown_game_mode_is_404(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    response = client.post(
        "/api/v1/servers/1/game-modes/ze/preflight",
        json={"wipe_addons": False},
    )
    assert response.status_code == 404
