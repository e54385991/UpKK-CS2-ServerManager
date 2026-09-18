"""Administrator marketplace editing: framework sections, edits, README sync."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_user, get_db
from modules.models.plugins import PluginCategory, PluginFramework
from services.plugins.description_sync_store import DescriptionSyncJobSnapshot, now


def _database_session():
    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

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
        "framework": PluginFramework.COUNTERSTRIKESHARP,
        "tags": None,
        "is_recommended": False,
        "icon_url": None,
        "github_url": "https://github.com/shobhit-pathak/MatchZy",
        "custom_install_path": None,
        "ai_metadata": None,
        "download_count": 0,
        "install_count": 0,
        "dependencies": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _description_job(**overrides) -> DescriptionSyncJobSnapshot:
    values = {
        "operation_id": "11111111-1111-1111-1111-111111111111",
        "actor_user_id": 1,
        "status": "queued",
        "command": "Sync plugin descriptions",
        "framework": "counterstrikesharp",
        "overwrite": True,
        "created_at": now(),
        "started_at": None,
        "completed_at": None,
        "phase": "queued",
        "message": "Queued 2 plugin descriptions",
        "current_plugin_id": None,
        "current_plugin_title": None,
        "current_github_url": None,
        "stop_reason": None,
        "retry_at": None,
        "cancel_requested": False,
        "target_ids": [11, 12],
        "total": 2,
        "processed": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
        "items": [],
    }
    values.update(overrides)
    return DescriptionSyncJobSnapshot(**values)


def _client(*, is_admin: bool = True):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="root", is_admin=is_admin, is_active=True)
    session = _database_session()

    async def override_db():
        yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = override_db
    return TestClient(app), user


def test_market_list_filters_by_framework(monkeypatch):
    client, _user = _client(is_admin=False)
    search = AsyncMock(return_value=([_sample_market(framework=PluginFramework.SWIFTLY)], 1))
    monkeypatch.setattr("api.routes.v1.plugins.MarketPlugin.search_plugins", search)
    monkeypatch.setattr("api.routes.v1.plugins.MarketPlugin.get_by_ids", AsyncMock(return_value=[]))

    response = client.get("/api/v1/plugins/market?framework=swiftly")

    assert response.status_code == 200
    assert response.json()["items"][0]["framework"] == "swiftly"
    assert search.await_args.kwargs["framework"] is PluginFramework.SWIFTLY
    # `other` is its own browse section, so a runtime section is an exact match
    # and framework-agnostic listings never leak into it.
    assert "include_framework_agnostic" not in search.await_args.kwargs
    assert search.await_args.kwargs["sort"] == "recommended"


def test_market_list_supports_time_ordering(monkeypatch):
    client, _user = _client(is_admin=False)
    search = AsyncMock(return_value=([_sample_market()], 1))
    monkeypatch.setattr("api.routes.v1.plugins.MarketPlugin.search_plugins", search)
    monkeypatch.setattr("api.routes.v1.plugins.MarketPlugin.get_by_ids", AsyncMock(return_value=[]))

    response = client.get("/api/v1/plugins/market?sort=newest")

    assert response.status_code == 200
    assert search.await_args.kwargs["sort"] == "newest"
    assert client.get("/api/v1/plugins/market?sort=alphabetical").status_code == 422


def test_market_list_keeps_other_section_separate(monkeypatch):
    client, _user = _client(is_admin=False)
    search = AsyncMock(return_value=([_sample_market(framework=PluginFramework.OTHER)], 1))
    monkeypatch.setattr("api.routes.v1.plugins.MarketPlugin.search_plugins", search)
    monkeypatch.setattr("api.routes.v1.plugins.MarketPlugin.get_by_ids", AsyncMock(return_value=[]))

    response = client.get("/api/v1/plugins/market?framework=other")

    assert response.status_code == 200
    assert response.json()["items"][0]["framework"] == "other"
    assert search.await_args.kwargs["framework"] is PluginFramework.OTHER


def test_market_list_rejects_unknown_framework():
    client, _user = _client(is_admin=False)

    response = client.get("/api/v1/plugins/market?framework=sourcemod")

    assert response.status_code == 400
    assert "counterstrikesharp" in response.json()["detail"]


def test_market_create_defaults_to_counterstrikesharp(monkeypatch):
    client, _user = _client()
    create = AsyncMock(return_value=_sample_market(id=31))
    monkeypatch.setattr("api.routes.v1.plugins.legacy.create_plugin", create)
    monkeypatch.setattr("api.routes.v1.plugins._dependency_refs", AsyncMock(return_value=[[]]))
    monkeypatch.setattr("api.routes.v1.plugins.record_audit_event", AsyncMock())

    response = client.post(
        "/api/v1/plugins/market",
        json={"github_url": "https://github.com/example/plugin", "title": "Plugin"},
    )

    assert response.status_code == 201
    assert response.json()["framework"] == "counterstrikesharp"
    assert create.await_args.args[0].framework == "counterstrikesharp"


def test_market_create_accepts_swiftly_section(monkeypatch):
    client, _user = _client()
    create = AsyncMock(return_value=_sample_market(framework=PluginFramework.SWIFTLY))
    monkeypatch.setattr("api.routes.v1.plugins.legacy.create_plugin", create)
    monkeypatch.setattr("api.routes.v1.plugins._dependency_refs", AsyncMock(return_value=[[]]))
    monkeypatch.setattr("api.routes.v1.plugins.record_audit_event", AsyncMock())

    response = client.post(
        "/api/v1/plugins/market",
        json={
            "github_url": "https://github.com/example/plugin",
            "title": "Plugin",
            "framework": "swiftly",
        },
    )

    assert response.status_code == 201
    assert create.await_args.args[0].framework == "swiftly"


def test_market_create_rejects_unknown_framework():
    client, _user = _client()

    response = client.post(
        "/api/v1/plugins/market",
        json={
            "github_url": "https://github.com/example/plugin",
            "title": "Plugin",
            "framework": "sourcemod",
        },
    )

    assert response.status_code == 422


def test_market_update_applies_submitted_fields_and_audits(monkeypatch):
    client, _user = _client()
    updated = _sample_market(
        description="# MatchZy", framework=PluginFramework.SWIFTLY, title="MatchZy"
    )
    update = AsyncMock(return_value=updated)
    audit = AsyncMock()
    monkeypatch.setattr("api.routes.v1.plugins.legacy.update_plugin", update)
    monkeypatch.setattr("api.routes.v1.plugins._dependency_refs", AsyncMock(return_value=[[]]))
    monkeypatch.setattr("api.routes.v1.plugins.record_audit_event", audit)

    response = client.patch(
        "/api/v1/plugins/market/11",
        json={"description": "# MatchZy", "framework": "swiftly"},
    )

    assert response.status_code == 200
    assert response.json()["framework"] == "swiftly"
    body = update.await_args.args[1]
    assert body.description == "# MatchZy"
    assert body.framework == "swiftly"
    # Untouched fields must stay unset so the legacy applier leaves them alone.
    assert body.title is None
    assert body.category is None
    assert audit.await_args.kwargs["action"] == "plugin.catalog.update"
    assert audit.await_args.kwargs["details"]["fields"] == ["description", "framework"]


def test_market_update_requires_at_least_one_field(monkeypatch):
    client, _user = _client()
    update = AsyncMock()
    monkeypatch.setattr("api.routes.v1.plugins.legacy.update_plugin", update)

    response = client.patch("/api/v1/plugins/market/11", json={})

    assert response.status_code == 400
    update.assert_not_awaited()


def test_market_update_rejects_empty_title():
    client, _user = _client()

    response = client.patch("/api/v1/plugins/market/11", json={"title": "   "})

    assert response.status_code == 422


def test_market_update_is_admin_only():
    client, _user = _client(is_admin=False)

    response = client.patch("/api/v1/plugins/market/11", json={"title": "Renamed"})

    assert response.status_code == 403


def test_market_description_sync_queues_job(monkeypatch):
    client, _user = _client()
    sync = AsyncMock(return_value=_description_job())
    audit = AsyncMock()
    monkeypatch.setattr("api.routes.v1.plugins.description_sync_store.enqueue", sync)
    monkeypatch.setattr("api.routes.v1.plugins.record_audit_event", audit)

    response = client.post(
        "/api/v1/plugins/market/descriptions/sync",
        json={
            "request_id": "22222222-2222-2222-2222-222222222222",
            "framework": "counterstrikesharp",
            "overwrite": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == "11111111-1111-1111-1111-111111111111"
    assert body["status"] == "queued"
    assert body["total"] == 2
    assert sync.await_args.kwargs["framework"] is PluginFramework.COUNTERSTRIKESHARP
    assert sync.await_args.kwargs["plugin_ids"] is None
    assert audit.await_args.kwargs["action"] == "plugin.catalog.sync_descriptions.requested"
    assert audit.await_args.kwargs["status"] == "requested"


def test_market_description_sync_passes_selected_ids(monkeypatch):
    client, _user = _client()
    sync = AsyncMock(return_value=_description_job(target_ids=[11, 12], total=2))
    monkeypatch.setattr("api.routes.v1.plugins.description_sync_store.enqueue", sync)
    monkeypatch.setattr("api.routes.v1.plugins.record_audit_event", AsyncMock())

    response = client.post(
        "/api/v1/plugins/market/descriptions/sync",
        json={
            "request_id": "22222222-2222-2222-2222-222222222222",
            "plugin_ids": [11, 11, 12],
            "overwrite": False,
        },
    )

    assert response.status_code == 202
    assert sync.await_args.kwargs["plugin_ids"] == [11, 12]
    assert sync.await_args.kwargs["overwrite"] is False


def test_market_description_sync_is_admin_only():
    client, _user = _client(is_admin=False)

    response = client.post(
        "/api/v1/plugins/market/descriptions/sync",
        json={"request_id": "22222222-2222-2222-2222-222222222222"},
    )

    assert response.status_code == 403


def test_market_description_sync_rejects_invalid_plugin_ids():
    client, _user = _client()

    response = client.post(
        "/api/v1/plugins/market/descriptions/sync",
        json={
            "request_id": "22222222-2222-2222-2222-222222222222",
            "plugin_ids": [0],
        },
    )

    assert response.status_code == 422


def test_market_description_sync_status_cancel_and_delete(monkeypatch):
    client, _user = _client()
    operation_id = "11111111-1111-1111-1111-111111111111"
    job = _description_job()
    monkeypatch.setattr(
        "api.routes.v1.plugins.description_sync_store.get_job",
        AsyncMock(return_value=job),
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.description_sync_store.cancel_job",
        AsyncMock(return_value=_description_job(status="running", cancel_requested=True)),
    )
    delete = AsyncMock()
    monkeypatch.setattr("api.routes.v1.plugins.description_sync_store.delete_job", delete)

    status = client.get(f"/api/v1/plugins/market/descriptions/sync/{operation_id}")
    cancelled = client.post(f"/api/v1/plugins/market/descriptions/sync/{operation_id}/cancel")
    deleted = client.delete(f"/api/v1/plugins/market/descriptions/sync/{operation_id}")

    assert status.status_code == 200
    assert status.json()["operation_id"] == operation_id
    assert cancelled.status_code == 200
    assert cancelled.json()["cancel_requested"] is True
    assert deleted.status_code == 200
    delete.assert_awaited_once_with(operation_id, 1)


def test_market_update_changes_both_classifications(monkeypatch):
    """The edit dialog must be able to move a listing between category and runtime."""
    client, _user = _client()
    updated = _sample_market(category=PluginCategory.UTILITY, framework=PluginFramework.OTHER)
    update = AsyncMock(return_value=updated)
    monkeypatch.setattr("api.routes.v1.plugins.legacy.update_plugin", update)
    monkeypatch.setattr("api.routes.v1.plugins._dependency_refs", AsyncMock(return_value=[[]]))
    monkeypatch.setattr("api.routes.v1.plugins.record_audit_event", AsyncMock())

    response = client.patch(
        "/api/v1/plugins/market/11",
        json={"category": "utility", "framework": "other"},
    )

    assert response.status_code == 200
    assert response.json()["category"] == "utility"
    assert response.json()["framework"] == "other"
    body = update.await_args.args[1]
    assert body.category == "utility"
    assert body.framework == "other"


def test_market_update_accepts_archive_installation_mapping(monkeypatch):
    client, _user = _client()
    updated = _sample_market(
        ai_metadata={
            "model": "administrator",
            "reviewed": True,
            "installation": {
                "asset_glob": "linux.zip",
                "automatic": False,
                "mappings": [{"source": "bundle", "target": "addons/custom"}],
                "source_prefix": "",
                "target_path": None,
            },
            "requirements": [],
            "notes": [],
            "sources": [],
        }
    )
    update = AsyncMock(return_value=updated)
    monkeypatch.setattr("api.routes.v1.plugins.legacy.update_plugin", update)
    monkeypatch.setattr("api.routes.v1.plugins._dependency_refs", AsyncMock(return_value=[[]]))
    monkeypatch.setattr("api.routes.v1.plugins.record_audit_event", AsyncMock())

    response = client.patch(
        "/api/v1/plugins/market/11",
        json={
            "installation": {
                "asset_glob": "linux.zip",
                "automatic": False,
                "mappings": [{"source": "bundle", "target": "addons/custom"}],
                "source_prefix": "",
                "target_path": None,
            }
        },
    )

    assert response.status_code == 200
    assert update.await_args.args[1].installation.mappings[0].target == "addons/custom"


def test_market_bulk_delete_requires_explicit_target():
    client, _user = _client()

    response = client.post("/api/v1/plugins/market/bulk-delete", json={})

    assert response.status_code == 422


def test_market_update_applies_classifications_to_the_row():
    """`apply_market_plugin_update` is what the legacy handler runs for a PATCH."""
    from modules.models.plugins import MarketPlugin
    from modules.plugin_ai import InstallationConfig, InstallationMapping, PluginAIInfo
    from modules.schemas.plugins import MarketPluginUpdate
    from services.plugins.catalog_fields import apply_market_plugin_update

    plugin = MarketPlugin(
        id=11,
        github_url="https://github.com/acme/plugin",
        title="Plugin",
        category=PluginCategory.GAME_MODE,
        framework=PluginFramework.COUNTERSTRIKESHARP,
    )

    apply_market_plugin_update(plugin, MarketPluginUpdate(category="library", framework="swiftly"))

    assert plugin.category is PluginCategory.LIBRARY
    assert plugin.framework is PluginFramework.SWIFTLY
    # Untouched fields keep their stored value.
    assert plugin.title == "Plugin"

    apply_market_plugin_update(
        plugin,
        MarketPluginUpdate(
            installation=InstallationConfig(
                mappings=[InstallationMapping(source="bundle", target="addons/custom")]
            )
        ),
    )
    stored = PluginAIInfo.model_validate(plugin.ai_metadata)
    assert stored.reviewed is True
    assert stored.installation is not None
    assert stored.installation.mappings[0].target == "addons/custom"


def test_market_update_rejects_an_unknown_classification():
    client, _user = _client()

    bad_category = client.patch("/api/v1/plugins/market/11", json={"category": "nope"})
    bad_framework = client.patch("/api/v1/plugins/market/11", json={"framework": "sourcemod"})

    assert bad_category.status_code == 422
    assert bad_framework.status_code == 422
