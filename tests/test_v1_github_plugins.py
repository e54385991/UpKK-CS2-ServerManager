"""Coverage for versioned GitHub URL plugin inspect + 202 install."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client(*, monkeypatch):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.require_server_access",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    return TestClient(app), user


def test_v1_github_releases_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get(
        "/api/v1/plugins/github/releases",
        params={"repo_url": "https://github.com/Source2ZE/CS2Fixes"},
    )
    assert response.status_code == 401


def test_v1_github_releases_returns_projection(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.list_legacy_releases",
        AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                error=None,
                repo_owner="Source2ZE",
                repo_name="CS2Fixes",
                linux_runtime_profile=None,
                releases=[
                    SimpleNamespace(
                        id="1",
                        tag_name="v1.2.0",
                        name="CS2Fixes",
                        published_at="2026-08-01T00:00:00Z",
                        prerelease=False,
                        assets=[
                            SimpleNamespace(
                                name="cs2fixes-linux.zip",
                                browser_download_url=(
                                    "https://github.com/Source2ZE/CS2Fixes/releases/"
                                    "download/v1.2.0/cs2fixes-linux.zip"
                                ),
                                size=12,
                                content_type="application/zip",
                                steam_runtime="steamrt3",
                                runtime_compatibility="recommended",
                            )
                        ],
                    )
                ],
            )
        ),
    )
    response = client.get(
        "/api/v1/plugins/github/releases",
        params={"repo_url": "https://github.com/Source2ZE/CS2Fixes", "server_id": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["repo_owner"] == "Source2ZE"
    assert body["releases"][0]["tag_name"] == "v1.2.0"
    assert body["releases"][0]["assets"][0]["name"] == "cs2fixes-linux.zip"


def test_v1_github_install_returns_202(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.enforce_agent_rate_limit",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.build_github_install_plan",
        AsyncMock(
            return_value={
                "server_id": 1,
                "repo_url": "https://github.com/Source2ZE/CS2Fixes",
                "mode": "install",
                "config_policy": "preserve",
                "plan_hash": "a" * 64,
                "mapping_required": False,
                "hard_conflicts": [],
                "release": {"tag": "v1.2.0"},
                "asset": {"name": "cs2fixes-linux.zip"},
            }
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.redis_manager.get",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.maintenance_lock_service.is_locked",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.enqueue_github_plugin_install",
        AsyncMock(
            return_value={
                "operation_id": operation_id,
                "server_id": 1,
                "action": "install_github_plugin",
                "status": "queued",
                "success": None,
                "message": None,
                "server_status": None,
                "actor_user_id": user.id,
                "started_at": "2026-08-29T00:00:00+00:00",
                "completed_at": None,
            }
        ),
    )
    response = client.post(
        "/api/v1/servers/1/plugins/github/install",
        json={
            "repo_url": "https://github.com/Source2ZE/CS2Fixes",
            "asset_name": "cs2fixes-linux.zip",
            "expected_plan_hash": "a" * 64,
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == operation_id
    assert body["action"] == "install_github_plugin"
    assert body["stream_url"] == f"/api/v1/servers/1/operations/{operation_id}/events"


def test_v1_github_install_blocked_when_mapping_required(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.enforce_agent_rate_limit",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.build_github_install_plan",
        AsyncMock(
            return_value={
                "server_id": 1,
                "plan_hash": "a" * 64,
                "mapping_required": True,
                "hard_conflicts": [],
            }
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.redis_manager.get",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.maintenance_lock_service.is_locked",
        AsyncMock(return_value=False),
    )
    response = client.post(
        "/api/v1/servers/1/plugins/github/install",
        json={
            "repo_url": "https://github.com/Source2ZE/CS2Fixes",
            "expected_plan_hash": "a" * 64,
        },
    )
    assert response.status_code == 409


def test_v1_github_plan_exposes_mapping(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.enforce_agent_rate_limit",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.build_github_install_plan",
        AsyncMock(
            return_value={
                "server_id": 1,
                "repo_url": "https://github.com/example/plugin",
                "mode": "install",
                "config_policy": "preserve",
                "plan_hash": "b" * 64,
                "mapping_required": False,
                "source_prefix": "payload",
                "mapping": [{"source": "payload", "target": "addons"}],
                "recipe_id": None,
                "exclude_dirs": ["configs"],
                "exclude_files": ["readme.txt"],
                "hard_conflicts": [],
                "conflict_warnings": [],
                "warnings": [],
                "compatibility_unknown": True,
                "already_installed": [],
                "dependencies": [],
                "release": {"tag": "v1.0.0", "name": "v1.0.0"},
                "asset": {"name": "plugin-linux.zip"},
                "archive_sha256": "c" * 64,
            }
        ),
    )
    response = client.post(
        "/api/v1/servers/1/plugins/github/plan",
        json={
            "repo_url": "https://github.com/example/plugin",
            "asset_name": "plugin-linux.zip",
            "source_prefix": "payload",
            "target_prefix": "addons",
            "exclude_dirs": ["configs"],
            "exclude_files": ["readme.txt"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mapping_required"] is False
    assert body["source_prefix"] == "payload"
    assert body["mapping"] == [{"source": "payload", "target": "addons"}]
    assert body["exclude_dirs"] == ["configs"]
    assert body["exclude_files"] == ["readme.txt"]


def test_v1_github_install_accepts_operator_mapping(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.enforce_agent_rate_limit",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.build_github_install_plan",
        AsyncMock(
            return_value={
                "server_id": 1,
                "repo_url": "https://github.com/example/plugin",
                "mode": "install",
                "config_policy": "preserve",
                "plan_hash": "d" * 64,
                "mapping_required": False,
                "hard_conflicts": [],
                "release": {"tag": "v1.0.0"},
                "asset": {"name": "plugin-linux.zip"},
            }
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.redis_manager.get",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.maintenance_lock_service.is_locked",
        AsyncMock(return_value=False),
    )
    enqueue = AsyncMock(
        return_value={
            "operation_id": operation_id,
            "server_id": 1,
            "action": "install_github_plugin",
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
        "api.routes.v1.github_plugins.enqueue_github_plugin_install",
        enqueue,
    )
    response = client.post(
        "/api/v1/servers/1/plugins/github/install",
        json={
            "repo_url": "https://github.com/example/plugin",
            "asset_name": "plugin-linux.zip",
            "expected_plan_hash": "d" * 64,
            "source_prefix": "payload",
            "target_prefix": "addons",
            "exclude_dirs": ["configs"],
        },
    )
    assert response.status_code == 202
    enqueue.assert_awaited_once()
    kwargs = enqueue.await_args.kwargs
    assert kwargs["source_prefix"] == "payload"
    assert kwargs["target_prefix"] == "addons"
    assert kwargs["exclude_dirs"] == ["configs"]


def test_v1_github_uninstall_returns_202(monkeypatch):
    client, user = _client(monkeypatch=monkeypatch)
    operation_id = str(uuid4())
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.redis_manager.get",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.github_plugins.maintenance_lock_service.is_locked",
        AsyncMock(return_value=False),
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
        "api.routes.v1.github_plugins.enqueue_github_plugin_uninstall",
        enqueue,
    )
    response = client.post(
        "/api/v1/servers/1/plugins/github/uninstall",
        json={"files_to_delete": ["addons/demo/plugin.dll", "addons/demo"]},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["action"] == "uninstall_github_plugin"
    assert body["stream_url"] == f"/api/v1/servers/1/operations/{operation_id}/events"
    enqueue.assert_awaited_once()
    kwargs = enqueue.await_args.kwargs
    assert kwargs["files_to_delete"] == ["addons/demo/plugin.dll", "addons/demo"]


def test_v1_github_uninstall_rejects_traversal(monkeypatch):
    client, _user = _client(monkeypatch=monkeypatch)
    response = client.post(
        "/api/v1/servers/1/plugins/github/uninstall",
        json={"files_to_delete": ["../etc/passwd"]},
    )
    assert response.status_code == 422
