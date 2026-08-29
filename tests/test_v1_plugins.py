"""Coverage for the versioned ``/api/v1`` plugin market and install contract."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from modules.models.plugins import PluginCategory
from modules.models.servers import ServerStatus


def _database_session(*, rows=None):
    class _Result:
        def scalars(self):
            return self

        def all(self):
            return list(rows or [])

    return SimpleNamespace(
        add=lambda *_a, **_k: None,
        commit=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(return_value=_Result()),
    )


def _sample_market(**overrides):
    values = {
        "id": 11,
        "title": "MatchZy",
        "description": "Practice plugin",
        "author": "shobhit",
        "version": "0.8.0",
        "category": PluginCategory.GAME_MODE,
        "tags": "practice,match",
        "is_recommended": True,
        "icon_url": None,
        "github_url": "https://github.com/shobhit-pathak/MatchZy",
        "download_count": 12,
        "install_count": 4,
        "dependencies": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _sample_managed(**overrides):
    values = {
        "id": 3,
        "server_id": 1,
        "source_type": "market",
        "source_key": "11",
        "display_name": "MatchZy",
        "repo_url": "https://github.com/shobhit-pathak/MatchZy",
        "market_plugin_id": 11,
        "framework_key": None,
        "installed_version": "0.8.0",
        "latest_version": "0.8.1",
        "auto_update_enabled": False,
        "last_status": "ok",
        "last_error": None,
        "last_check_at": datetime.now(timezone.utc),
        "last_update_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _sample_server(**overrides):
    values = {
        "id": 1,
        "name": "alpha",
        "host": "10.0.0.1",
        "game_port": 27015,
        "status": ServerStatus.STOPPED,
        "user_id": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _sample_plan(**overrides) -> dict:
    plan = {
        "server_id": 1,
        "plugin": {"id": 11, "title": "MatchZy"},
        "dependencies": [],
        "installation_order": [11],
        "already_installed": [],
        "tracking_records_without_remote_evidence": [],
        "compatibility_unknown": [],
        "hard_conflicts": [],
        "warnings": [],
        "steps": [
            {
                "order": 1,
                "plugin_id": 11,
                "title": "MatchZy",
                "kind": "target",
                "status": "install",
                "reason": "not_found_on_server",
            }
        ],
        "blocked": False,
        "plan_hash": "abc123",
    }
    plan.update(overrides)
    return plan


def _client(*, monkeypatch, session=None):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    db_session = session or _database_session()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        "api.routes.v1.plugins.require_server_access",
        AsyncMock(return_value=_sample_server()),
    )
    return TestClient(app), user


def test_v1_plugins_market_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/plugins/market")
    assert response.status_code == 401


def test_v1_plugins_market_list_and_categories(monkeypatch):
    plugin = _sample_market()
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.plugins.MarketPlugin.search_plugins",
        AsyncMock(return_value=([plugin], 1)),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.MarketPlugin.get_by_ids",
        AsyncMock(return_value=[]),
    )

    listed = client.get("/api/v1/plugins/market?q=match")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "MatchZy"
    assert body["items"][0]["category"] == "game_mode"
    assert body["items"][0]["github_url"].startswith("https://github.com/")
    assert "ssh_password" not in listed.text

    categories = client.get("/api/v1/plugins/market/categories")
    assert categories.status_code == 200
    values = {item["value"] for item in categories.json()["items"]}
    assert "game_mode" in values
    assert "utility" in values


def test_v1_plugins_market_rejects_unknown_category(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    response = client.get("/api/v1/plugins/market?category=not-a-category")
    assert response.status_code == 400


def test_v1_plugins_market_detail_404(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.plugins.MarketPlugin.get_by_id",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/v1/plugins/market/99")
    assert response.status_code == 404


def test_v1_server_plugins_list(monkeypatch):
    managed = _sample_managed()
    client, _user = _client(
        monkeypatch=monkeypatch,
        session=_database_session(rows=[managed]),
    )
    response = client.get("/api/v1/servers/1/plugins")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["display_name"] == "MatchZy"
    assert body[0]["installed_version"] == "0.8.0"
    assert "exclude_dirs" not in body[0]


def test_v1_plugin_preflight(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    planner = AsyncMock(return_value=_sample_plan())
    monkeypatch.setattr(
        "api.routes.v1.plugins.MarketPlugin.get_by_id",
        AsyncMock(return_value=_sample_market()),
    )
    monkeypatch.setattr("api.routes.v1.plugins.build_plugin_install_plan", planner)
    response = client.get("/api/v1/servers/1/plugins/market/11/preflight")
    assert response.status_code == 200
    body = response.json()
    assert body["plugin"]["title"] == "MatchZy"
    assert body["blocked"] is False
    assert body["plan_hash"] == "abc123"
    assert body["steps"][0]["status"] == "install"
    assert planner.await_args.kwargs["include_dependencies"] is False


def test_v1_plugin_preflight_can_include_dependencies(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    planner = AsyncMock(return_value=_sample_plan())
    monkeypatch.setattr(
        "api.routes.v1.plugins.MarketPlugin.get_by_id",
        AsyncMock(return_value=_sample_market()),
    )
    monkeypatch.setattr("api.routes.v1.plugins.build_plugin_install_plan", planner)
    response = client.get("/api/v1/servers/1/plugins/market/11/preflight?install_dependencies=true")
    assert response.status_code == 200
    assert planner.await_args.kwargs["include_dependencies"] is True


def test_v1_plugin_install_returns_202(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.plugins.MarketPlugin.get_by_id",
        AsyncMock(return_value=_sample_market()),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.build_plugin_install_plan",
        AsyncMock(return_value=_sample_plan()),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.validate_plugin_plan_acknowledgements",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.reject_stuck_lock_unless_active",
        AsyncMock(return_value=None),
    )
    enqueue = AsyncMock(
        return_value={
            "operation_id": operation_id,
            "server_id": 1,
            "action": "install_plugin",
            "status": "queued",
            "success": None,
            "message": None,
            "server_status": None,
            "actor_user_id": user.id,
            "started_at": "2026-08-29T00:00:00+00:00",
            "completed_at": None,
        }
    )
    monkeypatch.setattr("api.routes.v1.plugins.enqueue_plugin_install", enqueue)

    response = client.post("/api/v1/servers/1/plugins/market/11/install", json={})
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == operation_id
    assert body["action"] == "install_plugin"
    assert body["status"] == "queued"
    assert body["stream_url"] == f"/api/v1/servers/1/operations/{operation_id}/events"
    assert enqueue.await_args.kwargs["install_dependencies"] is False
    assert enqueue.await_args.kwargs["upgrade_mode"] is False
    assert enqueue.await_args.kwargs["download_url"] is None


def test_v1_plugin_install_forwards_web_parity_options(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    operation_id = str(uuid4())
    planner = AsyncMock(return_value=_sample_plan())
    enqueue = AsyncMock(
        return_value={
            "operation_id": operation_id,
            "server_id": 1,
            "action": "install_plugin",
            "status": "queued",
            "success": None,
            "message": None,
            "server_status": None,
            "actor_user_id": user.id,
            "started_at": "2026-08-29T00:00:00+00:00",
            "completed_at": None,
        }
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.MarketPlugin.get_by_id",
        AsyncMock(return_value=_sample_market()),
    )
    monkeypatch.setattr("api.routes.v1.plugins.build_plugin_install_plan", planner)
    monkeypatch.setattr(
        "api.routes.v1.plugins.validate_plugin_plan_acknowledgements",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.reject_stuck_lock_unless_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("api.routes.v1.plugins.enqueue_plugin_install", enqueue)

    download_url = "https://github.com/shobhit-pathak/MatchZy/releases/download/v0.8.1/MatchZy.zip"
    response = client.post(
        "/api/v1/servers/1/plugins/market/11/install",
        json={
            "download_url": download_url,
            "upgrade_mode": True,
            "install_dependencies": True,
            "exclude_dirs": ["cfg/old"],
            "exclude_files": ["README.md"],
            "plan_hash": "abc123",
        },
    )
    assert response.status_code == 202
    assert planner.await_args.kwargs["include_dependencies"] is True
    kwargs = enqueue.await_args.kwargs
    assert kwargs["download_url"] == download_url
    assert kwargs["upgrade_mode"] is True
    assert kwargs["install_dependencies"] is True
    assert kwargs["exclude_dirs"] == ["cfg/old"]
    assert kwargs["exclude_files"] == ["README.md"]
    assert kwargs["plan_hash"] == "abc123"


def test_v1_plugin_install_conflict_when_blocked(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.plugins.MarketPlugin.get_by_id",
        AsyncMock(return_value=_sample_market()),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.build_plugin_install_plan",
        AsyncMock(
            return_value=_sample_plan(
                blocked=True,
                hard_conflicts=[
                    {
                        "rule_id": 4,
                        "plugin_a_id": 11,
                        "plugin_b_id": 12,
                        "severity": "hard",
                        "reason": "incompatible",
                    }
                ],
            )
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.validate_plugin_plan_acknowledgements",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.reject_stuck_lock_unless_active",
        AsyncMock(return_value=None),
    )
    response = client.post("/api/v1/servers/1/plugins/market/11/install", json={})
    assert response.status_code == 409


def test_v1_market_uninstall_returns_202(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.plugins.MarketPlugin.get_by_id",
        AsyncMock(return_value=_sample_market()),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.reject_stuck_lock_unless_active",
        AsyncMock(return_value=None),
    )
    enqueue = AsyncMock(
        return_value={
            "operation_id": operation_id,
            "server_id": 1,
            "action": "uninstall_github_plugin",
            "status": "queued",
            "success": None,
            "message": None,
            "server_status": None,
            "actor_user_id": user.id,
            "started_at": "2026-08-29T00:00:00+00:00",
            "completed_at": None,
        }
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.enqueue_github_plugin_uninstall",
        enqueue,
    )
    response = client.post(
        "/api/v1/servers/1/plugins/market/11/uninstall",
        json={"files_to_delete": ["addons/matchzy/MatchZy.dll"]},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["action"] == "uninstall_github_plugin"
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["market_plugin_id"] == 11
