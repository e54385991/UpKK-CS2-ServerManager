"""Focused response-contract tests for the file-manager vertical slice."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api.application import create_app
from api.routes.file_manager import downloads as download_routes
from api.routes.file_manager import files as file_routes
from modules import get_current_active_user, get_db

JSON_SUCCESS_CONTRACTS = {
    ("/servers/{server_id}/files", "get"): ("200", "DirectoryListResponse"),
    ("/servers/{server_id}/files/content", "get"): ("200", "FileContentResponse"),
    ("/servers/{server_id}/files/content", "put"): ("200", "FileActionResponse"),
    ("/servers/{server_id}/files/upload", "post"): ("200", "FileUploadResponse"),
    (
        "/servers/{server_id}/files/download-ticket",
        "post",
    ): ("200", "DownloadTicketResponse"),
    (
        "/servers/{server_id}/files/download-url",
        "post",
    ): ("202", "DownloadUrlStartedResponse"),
    (
        "/servers/{server_id}/files/download-url/status/{task_id}",
        "get",
    ): ("200", "DownloadUrlStatusResponse"),
    ("/servers/{server_id}/files/mkdir", "post"): ("200", "DirectoryCreatedResponse"),
    ("/servers/{server_id}/files", "delete"): ("200", "FileActionResponse"),
    ("/servers/{server_id}/files/rename", "post"): ("200", "FileRenamedResponse"),
    (
        "/servers/{server_id}/files/archive/inspect",
        "post",
    ): ("200", "ArchiveInspectionResponse"),
    ("/servers/{server_id}/files/extract", "post"): ("200", "ExtractionStartedResponse"),
    (
        "/servers/{server_id}/files/extract/status/{task_id}",
        "get",
    ): ("200", "ExtractionStatusResponse"),
}


def test_file_manager_json_success_responses_have_explicit_openapi_models():
    paths = create_app(lifespan=None).openapi()["paths"]

    for (path, method), (status_code, model_name) in JSON_SUCCESS_CONTRACTS.items():
        schema = paths[path][method]["responses"][status_code]["content"]["application/json"][
            "schema"
        ]
        assert schema == {"$ref": f"#/components/schemas/{model_name}"}


def test_file_manager_openapi_declares_legacy_error_envelopes():
    paths = create_app(lifespan=None).openapi()["paths"]

    for (path, method), (success_status, _model_name) in JSON_SUCCESS_CONTRACTS.items():
        responses = paths[path][method]["responses"]
        assert {"401", "403", "404"} <= responses.keys()
        for status_code, response in responses.items():
            if status_code in {success_status, "422"}:
                continue
            schema = response["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorResponse"}

    assert "400" in paths["/servers/{server_id}/files/archive/inspect"]["post"]["responses"]
    assert "413" in paths["/servers/{server_id}/files/upload"]["post"]["responses"]
    for path in (
        "/servers/{server_id}/files/download-ticket",
        "/servers/{server_id}/files/download-url",
    ):
        assert "503" in paths[path]["post"]["responses"]
    for path, method in (
        ("/servers/{server_id}/files", "get"),
        ("/servers/{server_id}/files/content", "get"),
        ("/servers/{server_id}/files/content", "put"),
        ("/servers/{server_id}/files/upload", "post"),
        ("/servers/{server_id}/files/download-url", "post"),
        ("/servers/{server_id}/files/mkdir", "post"),
        ("/servers/{server_id}/files", "delete"),
        ("/servers/{server_id}/files/rename", "post"),
        ("/servers/{server_id}/files/archive/inspect", "post"),
        ("/servers/{server_id}/files/extract", "post"),
    ):
        assert "503" in paths[path][method]["responses"]

    for path, method in (
        ("/servers/{server_id}/files/content", "put"),
        ("/servers/{server_id}/files/upload", "post"),
        ("/servers/{server_id}/files/mkdir", "post"),
        ("/servers/{server_id}/files", "delete"),
        ("/servers/{server_id}/files/rename", "post"),
    ):
        assert "409" in paths[path][method]["responses"]

    binary_responses = paths["/servers/{server_id}/files/download"]["get"]["responses"]
    assert binary_responses["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


def test_binary_download_route_does_not_claim_a_json_response_model():
    route = next(
        route
        for route in file_routes.router.routes
        if getattr(route, "path", None) == "/servers/{server_id}/files/download"
        and "GET" in (getattr(route, "methods", None) or set())
    )

    assert route.response_model is None


@pytest.mark.asyncio
async def test_file_content_response_body_is_unchanged(monkeypatch):
    server = SimpleNamespace(game_directory="/srv/cs2")

    class FakeSSHManager:
        async def validate_path_within_base(self, *_args, **_kwargs):
            return True, ""

        async def read_file(self, *_args, **_kwargs):
            return True, 'hostname = "测试服"\n', ""

        async def disconnect(self):
            return None

    monkeypatch.setattr(
        file_routes,
        "get_server_for_user",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(file_routes, "SSHManager", FakeSSHManager)

    app = FastAPI()
    app.include_router(file_routes.router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=7)
    app.dependency_overrides[file_routes.get_ssh_manager] = FakeSSHManager

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/servers/1/files/content",
            params={"path": "/srv/cs2/game/csgo/cfg/server.cfg"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "path": "/srv/cs2/game/csgo/cfg/server.cfg",
        "content": 'hostname = "测试服"\n',
    }


@pytest.mark.asyncio
async def test_url_download_keeps_accepted_body_and_creates_a_uuid_task(monkeypatch):
    server = SimpleNamespace(game_directory="/srv/cs2")
    current_user = SimpleNamespace(id=7, is_admin=False)

    class FakeDatabase:
        async def commit(self):
            return None

    class FakeSSHManager:
        async def validate_path_within_base(self, *_args, **_kwargs):
            return True, ""

        async def disconnect(self):
            return None

    task_id = uuid.UUID("12345678-1234-5678-1234-567812345678")

    def discard_task(_request, coroutine, *, name):
        assert name == f"file-url-download-{task_id}"
        coroutine.close()
        return SimpleNamespace()

    monkeypatch.setattr(
        download_routes,
        "get_server_for_user",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(
        download_routes,
        "get_effective_github_token",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(download_routes, "SSHManager", FakeSSHManager)
    monkeypatch.setattr(download_routes.uuid, "uuid4", lambda: task_id)
    monkeypatch.setattr(download_routes, "_spawn_file_task", discard_task)
    download_routes.download_url_tasks.clear()
    download_routes._download_url_task_refs.clear()

    app = FastAPI()
    app.state.container = SimpleNamespace(
        http=SimpleNamespace(
            get=lambda *_args, **_kwargs: None,
            post=lambda *_args, **_kwargs: None,
            borrow_client=lambda: None,
        )
    )
    app.include_router(download_routes.router)
    app.dependency_overrides[get_db] = lambda: FakeDatabase()
    app.dependency_overrides[get_current_active_user] = lambda: current_user
    app.dependency_overrides[download_routes.get_ssh_manager] = FakeSSHManager
    app.dependency_overrides[download_routes.resolve_maintenance_lock_service] = lambda: (
        SimpleNamespace(get=lambda *_args, **_kwargs: None)
    )

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/servers/9/files/download-url",
                json={
                    "url": "https://example.com/bundle.zip",
                    "destination_path": "/srv/cs2/plugins",
                },
            )
    finally:
        download_routes.download_url_tasks.clear()
        download_routes._download_url_task_refs.clear()

    assert response.status_code == 202
    assert response.json() == {
        "success": True,
        "task_id": str(task_id),
        "status": "pending",
        "target_path": "/srv/cs2/plugins/bundle.zip",
    }
