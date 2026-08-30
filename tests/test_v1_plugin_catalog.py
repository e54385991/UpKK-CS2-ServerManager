"""Coverage for the versioned ``/api/v1/plugin-catalog`` contract."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from modules.schemas.plugins import (
    PluginCatalogEntry,
    PluginCatalogExport,
    PluginCatalogImportResponse,
    PluginCatalogImportResult,
)


def _database_session():
    return SimpleNamespace(add=lambda *_a, **_k: None, commit=AsyncMock(), refresh=AsyncMock())


async def _fake_db():
    yield _database_session()


def _client(*, admin: bool = False):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=admin, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), user


def _bundle() -> dict:
    export = PluginCatalogExport(
        exported_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        plugins=[
            PluginCatalogEntry(
                github_url="https://github.com/shobhit-pathak/MatchZy",
                title="MatchZy",
                category="game_mode",
                dependencies=["https://github.com/roflmuffin/CounterStrikeSharp"],
            )
        ],
        conflicts=[],
    )
    payload = export.model_dump(mode="json")
    payload["conflict_strategy"] = "skip"
    return payload


def test_v1_plugin_catalog_export_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/plugin-catalog")
    assert response.status_code == 401


def test_v1_plugin_catalog_import_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.post("/api/v1/plugin-catalog", json=_bundle())
    assert response.status_code == 401


def test_v1_plugin_catalog_import_is_admin_only():
    client, _user = _client(admin=False)
    response = client.post("/api/v1/plugin-catalog", json=_bundle())
    assert response.status_code == 403
    assert response.json()["detail"] == "Not enough permissions"


def test_v1_plugin_catalog_export_available_to_members(monkeypatch):
    client, _user = _client(admin=False)

    async def fake_export(_db):
        return PluginCatalogExport(
            exported_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            plugins=[
                PluginCatalogEntry(
                    github_url="https://github.com/shobhit-pathak/MatchZy",
                    title="MatchZy",
                    category="game_mode",
                )
            ],
        )

    monkeypatch.setattr(
        "api.routes.v1.plugin_catalog.collect_export_bundle",
        fake_export,
    )
    response = client.get("/api/v1/plugin-catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "upkk-cs2-plugin-catalog"
    assert body["version"] == 1
    assert body["plugins"][0]["github_url"].startswith("https://github.com/")
    assert "id" not in body["plugins"][0]
    assert "plugin_a_id" not in str(body)


def test_v1_plugin_catalog_import_returns_summary(monkeypatch):
    client, user = _client(admin=True)
    captured = {}

    async def fake_import(db, request):
        del db
        captured["strategy"] = request.conflict_strategy
        captured["count"] = len(request.plugins)
        return PluginCatalogImportResponse(
            total=1,
            imported=1,
            updated=0,
            skipped=0,
            failed=0,
            results=[
                PluginCatalogImportResult(
                    index=1,
                    kind="plugin",
                    name="MatchZy",
                    action="imported",
                    plugin_id=22,
                )
            ],
        )

    monkeypatch.setattr(
        "api.routes.v1.plugin_catalog.import_plugin_catalog",
        fake_import,
    )
    monkeypatch.setattr(
        "api.routes.v1.plugin_catalog.record_audit_event",
        AsyncMock(),
    )
    response = client.post("/api/v1/plugin-catalog", json=_bundle())
    assert response.status_code == 200
    body = response.json()
    assert captured["strategy"] == "skip"
    assert captured["count"] == 1
    assert user.is_admin is True
    assert body["imported"] == 1
    assert body["results"][0]["kind"] == "plugin"
    assert body["results"][0]["plugin_id"] == 22


def test_v1_plugin_catalog_import_rejects_invalid_bundle():
    client, _user = _client(admin=True)
    response = client.post(
        "/api/v1/plugin-catalog",
        json={"format": "not-a-catalog", "plugins": []},
    )
    assert response.status_code == 422


def test_v1_plugin_catalog_import_rejects_rename_strategy():
    client, _user = _client(admin=True)
    payload = _bundle()
    payload["conflict_strategy"] = "rename"
    response = client.post("/api/v1/plugin-catalog", json=payload)
    assert response.status_code == 422
