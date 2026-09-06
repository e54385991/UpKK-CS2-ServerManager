"""Administrator-only queue API, strict inputs and reconnectable snapshots."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.application import create_app
from api.dependencies import get_bearer_or_cookie_user
from modules import get_current_active_user, get_current_user, get_db
from modules.models import PluginImportJob
from modules.plugin_ai import GitHubVerification, ImportOptions
from services.plugins import ai_import_store as store

BASE = "/api/v1/plugins/market/ai-imports"


@pytest.fixture
def client(monkeypatch):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="admin", is_admin=True, is_active=True)

    async def database():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_bearer_or_cookie_user] = lambda: user
    job = store.snapshot(
        PluginImportJob(
            actor_user_id=1,
            created_at=store.now(),
            request_key="request",
            command="Import",
            options=ImportOptions().model_dump(),
        )
    )
    for method, value in [
        ("enqueue", job),
        ("get_job", job),
        ("cancel_job", replace(job, status="cancelled")),
        ("list_jobs", [job]),
        (
            "readiness",
            (GitHubVerification(valid=True, account="admin"), SimpleNamespace(model="test-model")),
        ),
        ("check_administrator", None),
    ]:
        monkeypatch.setattr(store, method, AsyncMock(return_value=value))
    return TestClient(app), user, job


def test_submit_202_and_presenter_never_exposes_actor_secrets(client):
    api, _, job = client
    body = {"request_id": str(uuid4()), "options": {}, "acknowledge_ai_warning": True}
    response = api.post(BASE, json=body)
    assert response.status_code == 202
    assert response.json()["operation_id"] == job.operation_id
    assert "actor_user_id" not in response.json()
    assert "token" not in response.text
    assert api.get(BASE).status_code == 200
    assert api.get(BASE + "/readiness").json()["ai_model"] == "test-model"
    assert api.post(BASE + f"/{job.operation_id}/cancel").json()["status"] == "cancelled"


def test_permission_and_strict_validation(client):
    api, user, _ = client
    body = {"request_id": str(uuid4()), "options": {}, "acknowledge_ai_warning": True}
    assert api.post(BASE, json={**body, "shell": "id"}).status_code == 422
    assert api.post(BASE, json={**body, "options": {"minutes": 121}}).status_code == 422
    assert api.post(BASE, json={**body, "acknowledge_ai_warning": False}).status_code == 422
    user.is_admin = False
    assert api.post(BASE, json=body).status_code == 403
    assert api.get(BASE).status_code == 403
    assert api.get(BASE + "/readiness").status_code == 403


def test_safe_queue_errors_and_missing_jobs(client):
    api, _, job = client
    for error, status in [
        (PermissionError("Verify token"), 409),
        (ValueError("Queue full"), 409),
        (RuntimeError("Redis unavailable"), 503),
    ]:
        store.enqueue.side_effect = error
        assert (
            api.post(
                BASE,
                json={"request_id": str(uuid4()), "options": {}, "acknowledge_ai_warning": True},
            ).status_code
            == status
        )
    store.get_job.return_value = None
    assert api.get(BASE + f"/{job.operation_id}").status_code == 404
    store.cancel_job.side_effect = LookupError("Missing")
    assert api.post(BASE + f"/{job.operation_id}/cancel").status_code == 404


def test_sse_reconnect_replays_complete_terminal_snapshot_and_checks_revocation(client):
    api, user, job = client
    store.get_job.return_value = replace(job, status="completed")
    url = BASE + f"/{job.operation_id}/events"
    first = api.get(url)
    assert first.status_code == 200 and "event: snapshot" in first.text
    assert "id: 0" in first.text
    assert api.get(url, headers={"Last-Event-ID": "0"}).text == first.text
    store.check_administrator.side_effect = PermissionError()
    assert api.get(url).text == ""
    user.is_admin = False
    assert api.get(url).status_code == 403


@pytest.mark.asyncio
async def test_activity_inbox_removes_catalog_tasks_after_admin_revocation(monkeypatch):
    from api.contracts.v1.operations import OperationInboxView
    from api.routes.v1 import operation_inbox

    user = SimpleNamespace(id=1, is_admin=True)
    request = SimpleNamespace(is_disconnected=AsyncMock(side_effect=[False, True]))
    monkeypatch.setattr(operation_inbox, "_list_inbox_servers", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        operation_inbox, "check_administrator", AsyncMock(side_effect=PermissionError())
    )
    build = AsyncMock(
        return_value=OperationInboxView(
            items=[], failed_items=[], active_count=0, running_count=0, failed_count=0
        )
    )
    monkeypatch.setattr(operation_inbox, "_build_inbox", build)
    response = await operation_inbox.stream_operation_inbox(request, user)
    chunks = [chunk async for chunk in response.body_iterator]
    assert len(chunks) == 2
    build.assert_awaited_once_with([], False)
