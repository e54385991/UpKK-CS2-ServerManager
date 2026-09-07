"""Coverage for GET /api/v1/operations/inbox."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.application import create_app
from api.dependencies import get_bearer_or_cookie_user
from api.routes.v1.operation_inbox import _build_inbox
from modules import get_current_active_user, get_current_user, get_db


def _client(monkeypatch, *, admin: bool = False):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="owner", is_admin=admin, is_active=True)

    class _Result:
        def all(self):
            return [(1, "alpha")]

    session = SimpleNamespace(execute=AsyncMock(return_value=_Result()))

    async def override_db():
        yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_bearer_or_cookie_user] = lambda: user
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_operation_inbox_lists_queued_job(monkeypatch):
    client = _client(monkeypatch)
    operation_id = str(uuid4())
    record = {
        "operation_id": operation_id,
        "server_id": 1,
        "action": "install_plugin",
        "command": "plugin-market install 11 --from latest",
        "status": "queued",
        "success": None,
        "message": None,
        "server_status": None,
        "actor_user_id": 1,
        "started_at": "2026-08-29T00:00:00+00:00",
        "completed_at": None,
    }
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.list_for_server",
        AsyncMock(return_value=[record]),
    )
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.latest_message",
        AsyncMock(return_value="Queued behind start (position 1)"),
    )
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.list_failed_for_server",
        AsyncMock(return_value=[]),
    )
    response = client.get("/api/v1/operations/inbox")
    assert response.status_code == 200
    body = response.json()
    assert body["active_count"] == 1
    assert body["running_count"] == 0
    assert body["failed_count"] == 0
    assert body["failed_retention_days"] == 7
    assert body["items"][0]["server_name"] == "alpha"
    assert body["items"][0]["command"] == "plugin-market install 11 --from latest"
    assert body["items"][0]["latest_message"].startswith("Queued")


def test_operation_inbox_lists_and_clears_failures(monkeypatch):
    client = _client(monkeypatch)
    operation_id = str(uuid4())
    failed = {
        "operation_id": operation_id,
        "server_id": 1,
        "action": "install_plugin",
        "command": "plugin-market install 11 --from latest",
        "status": "failed",
        "success": False,
        "message": "extract failed",
        "server_status": None,
        "actor_user_id": 1,
        "started_at": "2026-08-29T00:00:00+00:00",
        "completed_at": "2026-08-29T00:01:00+00:00",
    }
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.list_for_server",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.list_failed_for_server",
        AsyncMock(return_value=[failed]),
    )
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.latest_message",
        AsyncMock(return_value="extract failed"),
    )
    response = client.get("/api/v1/operations/inbox")
    assert response.status_code == 200
    body = response.json()
    assert body["failed_count"] == 1
    assert body["failed_items"][0]["command"] == "plugin-market install 11 --from latest"
    assert body["failed_items"][0]["status"] == "failed"

    cleared = AsyncMock(return_value=1)
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.clear_failed",
        cleared,
    )
    import_jobs = AsyncMock(return_value=2)
    monkeypatch.setattr("api.routes.v1.operation_inbox.clear_failed_jobs", import_jobs)
    wipe = client.delete("/api/v1/operations/inbox/failed")
    assert wipe.status_code == 200
    assert wipe.json()["success"] is True
    cleared.assert_awaited_once_with([1])
    # A member sees no AI import jobs, so none are cleared on their behalf.
    import_jobs.assert_not_awaited()

    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.get",
        AsyncMock(return_value=failed),
    )
    dismiss = AsyncMock(return_value=failed)
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.dismiss_failed",
        dismiss,
    )
    one = client.delete(f"/api/v1/operations/inbox/failed/{operation_id}")
    assert one.status_code == 200
    dismiss.assert_awaited_once()


def test_clear_failed_also_clears_admin_visible_ai_imports(monkeypatch):
    """A failed AI import shares the tray's red badge, so one click clears both."""
    client = _client(monkeypatch, admin=True)
    cleared = AsyncMock(return_value=1)
    monkeypatch.setattr("api.routes.v1.operation_inbox.server_operation_hub.clear_failed", cleared)
    import_jobs = AsyncMock(return_value=2)
    monkeypatch.setattr("api.routes.v1.operation_inbox.clear_failed_jobs", import_jobs)

    response = client.delete("/api/v1/operations/inbox/failed")

    assert response.status_code == 200
    assert "3" in response.json()["message"]
    import_jobs.assert_awaited_once_with(1)

    # Losing administrator rights mid-request must not fail the whole clear.
    import_jobs.reset_mock()
    import_jobs.side_effect = PermissionError("no longer an administrator")
    revoked = client.delete("/api/v1/operations/inbox/failed")
    assert revoked.status_code == 200
    assert "1" in revoked.json()["message"]


def test_operation_inbox_events_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/v1/operations/inbox/events")
    assert response.status_code == 401


def test_operation_inbox_events_route_is_registered():
    from fastapi.routing import iter_route_contexts

    app = create_app(lifespan=None)
    paths = {getattr(context.route, "path", None) for context in iter_route_contexts(app.routes)}
    assert "/api/v1/operations/inbox/events" in paths


@pytest.mark.asyncio
async def test_build_inbox_includes_command_and_status(monkeypatch):
    operation_id = str(uuid4())
    record = {
        "operation_id": operation_id,
        "server_id": 1,
        "action": "install_plugin",
        "command": "plugin-market install 11 --from latest",
        "status": "running",
        "success": None,
        "message": None,
        "server_status": None,
        "actor_user_id": 1,
        "started_at": "2026-08-29T00:00:00+00:00",
        "completed_at": None,
    }
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.list_for_server",
        AsyncMock(return_value=[record]),
    )
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.latest_message",
        AsyncMock(return_value="Extracting archive"),
    )
    monkeypatch.setattr(
        "api.routes.v1.operation_inbox.server_operation_hub.list_failed_for_server",
        AsyncMock(return_value=[]),
    )
    view = await _build_inbox([(1, "alpha")])
    assert view.active_count == 1
    assert view.running_count == 1
    assert view.items[0].command == "plugin-market install 11 --from latest"
    assert view.items[0].status == "running"
    assert view.items[0].latest_message == "Extracting archive"
