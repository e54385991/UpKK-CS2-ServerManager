"""Response-contract coverage for small JSON authentication/system/task routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api.application import create_app
from api.dependencies import get_unit_of_work
from api.routes import auth as auth_routes
from api.routes import captcha as captcha_routes
from api.routes import gmail_oauth as gmail_oauth_routes
from api.routes import public as public_routes
from api.routes import scheduled_tasks as scheduled_task_routes
from api.routes import setup as setup_routes
from api.routes import system_settings as system_settings_routes
from api.routes.actions import batch as batch_routes
from modules import get_current_active_user, get_current_admin_user, get_current_user, get_db
from modules.auth import get_current_principal

JSON_SUCCESS_CONTRACTS = {
    ("/ping", "get"): "PublicPingResponse",
    ("/a2s-cache-test", "get"): "A2STestResponse",
    ("/a2s-cache", "get"): "A2SCacheEnvelope",
    ("/api/captcha/generate", "get"): "CaptchaResponse",
    ("/api/captcha/refresh", "post"): "CaptchaResponse",
    ("/api/auth/google-config", "get"): "GoogleConfigResponse",
    ("/api/auth/session", "post"): "AuthSuccessResponse",
    ("/api/auth/reset-password", "post"): "AuthMessageResponse",
    ("/api/auth/api-key", "delete"): "AuthMessageResponse",
    ("/api/auth/s3-settings/test", "post"): "S3ConnectionTestResponse",
    ("/api/auth/forgot-password", "post"): "AuthMessageResponse",
    ("/api/auth/reset-password-with-token", "post"): "AuthMessageResponse",
    ("/api/auth/logout", "post"): "AuthSuccessResponse",
    ("/api/system/settings/test-email", "post"): "EmailTestResponse",
    ("/api/gmail-oauth/upload-credentials", "post"): "GmailOAuthActionResponse",
    ("/api/gmail-oauth/revoke", "delete"): "GmailOAuthActionResponse",
    ("/api/gmail-oauth/status", "get"): "GmailOAuthStatusResponse",
    ("/servers/batch-actions/{batch_id}", "get"): "BatchActionStatusResponse",
    (
        "/api/setup/initialized-servers/{server_key}",
        "delete",
    ): "OperationMessageResponse",
    (
        "/api/scheduled-tasks/{server_id}/tasks/{task_id}",
        "delete",
    ): "ScheduledTaskDeleteResponse",
}

ERROR_CONTRACTS = {
    ("/a2s-cache-test", "get"): {"401", "403", "503"},
    ("/a2s-cache", "get"): {"401", "503"},
    ("/api/captcha/generate", "get"): {"429"},
    ("/api/captcha/refresh", "post"): {"429"},
    ("/api/auth/session", "post"): {"401"},
    ("/api/auth/reset-password", "post"): {"400"},
    ("/api/auth/api-key", "delete"): {"404"},
    ("/api/auth/forgot-password", "post"): {"400", "500"},
    ("/api/auth/reset-password-with-token", "post"): {"400", "404"},
    ("/api/system/settings/test-email", "post"): {"500"},
    ("/api/gmail-oauth/upload-credentials", "post"): {
        "400",
        "401",
        "403",
        "500",
        "503",
    },
    ("/api/gmail-oauth/revoke", "delete"): {"401", "403", "500", "503"},
    ("/api/gmail-oauth/status", "get"): {"401", "403", "500", "503"},
    ("/servers/batch-actions/{batch_id}", "get"): {"401", "404"},
    (
        "/api/setup/initialized-servers/{server_key}",
        "delete",
    ): {"401", "403", "404", "500"},
    (
        "/api/scheduled-tasks/{server_id}/tasks/{task_id}",
        "delete",
    ): {"404"},
}


def test_small_json_routes_have_explicit_success_models_and_status_codes():
    app = create_app(lifespan=None)
    paths = app.openapi()["paths"]
    source_routes = (
        public_routes.router.routes
        + captcha_routes.router.routes
        + auth_routes.router.routes
        + gmail_oauth_routes.router.routes
        + batch_routes.router.routes
        + setup_routes.router.routes
        + system_settings_routes.router.routes
        + scheduled_task_routes.router.routes
    )

    for (path, method), model_name in JSON_SUCCESS_CONTRACTS.items():
        operation = paths[path][method]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": f"#/components/schemas/{model_name}"}

        route = next(
            route
            for route in source_routes
            if getattr(route, "path_format", getattr(route, "path", None)) == path
            and method.upper() in (getattr(route, "methods", None) or set())
        )
        assert route.status_code == 200


def test_small_json_routes_declare_their_explicit_error_envelopes():
    paths = create_app(lifespan=None).openapi()["paths"]

    for (path, method), expected_statuses in ERROR_CONTRACTS.items():
        responses = paths[path][method]["responses"]
        assert expected_statuses <= responses.keys()
        for status_code in expected_statuses:
            schema = responses[status_code]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorResponse"}


@pytest.mark.asyncio
async def test_auth_configuration_session_and_logout_bodies_remain_unchanged():
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=7)
    app.dependency_overrides[auth_routes.resolve_s3_backup_service] = lambda: (
        auth_routes.s3_backup_service
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        config_response = await client.get("/api/auth/google-config")
        session_response = await client.post(
            "/api/auth/session",
            headers={"Authorization": "Bearer test-session-token"},
        )
        logout_response = await client.post("/api/auth/logout")

    assert config_response.status_code == 200
    assert config_response.json() == {
        "client_id": auth_routes.settings.GOOGLE_CLIENT_ID,
        "enabled": bool(auth_routes.settings.GOOGLE_CLIENT_ID),
    }
    assert session_response.status_code == 200
    assert session_response.json() == {"success": True}
    assert logout_response.status_code == 200
    assert logout_response.json() == {"success": True}


@pytest.mark.asyncio
async def test_s3_probe_body_remains_unchanged(monkeypatch):
    steps = [
        {
            "name": "configuration",
            "status": "failed",
            "message": "S3-compatible storage is not fully configured.",
        }
    ]
    monkeypatch.setattr(
        auth_routes.s3_backup_service,
        "test_connection",
        AsyncMock(
            return_value=(
                False,
                "S3-compatible storage is not fully configured.",
                steps,
            )
        ),
    )

    app = FastAPI()
    app.include_router(auth_routes.router)
    configuration = SimpleNamespace(
        id=7,
        s3_enabled=False,
        s3_endpoint_url=None,
        s3_region=None,
        s3_bucket=None,
        s3_access_key_id=None,
        s3_secret_access_key=None,
        s3_prefix=None,
        s3_use_ssl=True,
        s3_retention_count=10,
    )
    uow = SimpleNamespace(
        session=SimpleNamespace(get=AsyncMock(return_value=configuration)),
        commit=AsyncMock(),
    )
    app.dependency_overrides[get_current_principal] = lambda: SimpleNamespace(id=7)
    app.dependency_overrides[get_unit_of_work] = lambda: uow
    app.dependency_overrides[auth_routes.resolve_s3_backup_service] = lambda: (
        auth_routes.s3_backup_service
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/auth/s3-settings/test")

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "S3-compatible storage is not fully configured.",
        "steps": steps,
    }
    uow.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_test_email_body_remains_unchanged(monkeypatch):
    monkeypatch.setattr(
        system_settings_routes.email_service,
        "send_email",
        AsyncMock(return_value=True),
    )

    app = FastAPI()
    app.include_router(system_settings_routes.router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(username="admin")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/system/settings/test-email",
            json={"test_email": "operator@example.com"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "Test email sent successfully to operator@example.com",
    }


@pytest.mark.asyncio
async def test_scheduled_task_delete_body_remains_unchanged(monkeypatch):
    class FakeDatabase:
        async def execute(self, _statement):
            return SimpleNamespace(rowcount=1)

        async def commit(self):
            return None

    monkeypatch.setattr(
        scheduled_task_routes,
        "get_server_for_user",
        AsyncMock(return_value=SimpleNamespace(id=9)),
    )
    monkeypatch.setattr(
        scheduled_task_routes.ScheduledTask,
        "get_by_id_and_server",
        AsyncMock(return_value=SimpleNamespace(action="restart")),
    )

    app = FastAPI()
    app.include_router(scheduled_task_routes.router)
    app.dependency_overrides[get_db] = FakeDatabase
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7, is_admin=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/scheduled-tasks/9/tasks/12")

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Scheduled task deleted"}
