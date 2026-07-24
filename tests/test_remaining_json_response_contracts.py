"""Precise OpenAPI and wire contracts for remaining ordinary JSON routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api.application import create_app
from api.response_models import (
    AllServersDiskSpaceResponse,
    CustomCommandDeleteResponse,
    CustomCommandExecutionResponse,
    DeploymentConfirmationResponse,
    DeploymentLockResponse,
    DeploymentProgressResponse,
    OperationMessageResponse,
    ServerActionResponse,
    SSHConnectionInfoResponse,
    SSHHealthResponse,
    SSHReconnectResponse,
    StartupCommandResponse,
)
from api.routes.actions import deployment as deployment_routes
from api.routes.servers import configuration as configuration_routes
from modules import get_current_active_user, get_db

SUCCESS_CONTRACTS = {
    ("/servers/{server_id}/ssh-connection-info", "get"): "SSHConnectionInfoResponse",
    ("/servers/{server_id}/reconnect-ssh", "post"): "OperationMessageResponse",
    ("/servers/{server_id}/reset-reconnect-counter", "post"): "OperationMessageResponse",
    ("/servers/{server_id}/deployment-lock", "get"): "DeploymentLockResponse",
    ("/servers/{server_id}/deployment-lock", "delete"): "OperationMessageResponse",
    ("/servers/{server_id}/actions", "post"): "ServerActionResponse",
    ("/servers/{server_id}/deployment-progress", "get"): "DeploymentProgressResponse",
    ("/servers/disk-space-all", "get"): "AllServersDiskSpaceResponse",
    ("/servers/{server_id}/confirm-deployment", "post"): "DeploymentConfirmationResponse",
    ("/servers/{server_id}/ssh-reconnect", "post"): "SSHReconnectResponse",
    ("/servers/{server_id}/ssh-health", "get"): "SSHHealthResponse",
    ("/servers/{server_id}/discord-settings/test", "post"): "OperationMessageResponse",
    (
        "/servers/{server_id}/custom-commands/{command_id}",
        "delete",
    ): "CustomCommandDeleteResponse",
    (
        "/servers/{server_id}/custom-commands/execute",
        "post",
    ): "CustomCommandExecutionResponse",
    (
        "/servers/{server_id}/custom-commands/{command_id}/execute",
        "post",
    ): "CustomCommandExecutionResponse",
    ("/servers/{server_id}/startup-command", "get"): "StartupCommandResponse",
}

ERROR_CONTRACTS = {
    ("/servers/{server_id}/ssh-connection-info", "get"): {"401", "404", "503"},
    ("/servers/{server_id}/reconnect-ssh", "post"): {"401", "404", "500", "503"},
    ("/servers/{server_id}/reset-reconnect-counter", "post"): {
        "401",
        "404",
        "500",
        "503",
    },
    ("/servers/{server_id}/deployment-lock", "get"): {"401", "404"},
    ("/servers/{server_id}/actions", "post"): {
        "401",
        "404",
        "409",
        "500",
        "503",
    },
    ("/servers/{server_id}/deployment-progress", "get"): {"401", "404"},
    ("/servers/{server_id}/confirm-deployment", "post"): {"401", "404", "409"},
    ("/servers/{server_id}/ssh-reconnect", "post"): {"401", "403", "404"},
    ("/servers/{server_id}/ssh-health", "get"): {"401", "403", "404"},
    ("/servers/{server_id}/discord-settings/test", "post"): {"400", "401", "404"},
    (
        "/servers/{server_id}/custom-commands/{command_id}",
        "delete",
    ): {"401", "404"},
    ("/servers/{server_id}/custom-commands/execute", "post"): {
        "400",
        "401",
        "404",
        "503",
    },
    (
        "/servers/{server_id}/custom-commands/{command_id}/execute",
        "post",
    ): {"400", "401", "404", "503"},
    ("/servers/{server_id}/startup-command", "get"): {"401", "404"},
}


def test_remaining_json_routes_use_named_success_models_and_explicit_status_codes() -> None:
    openapi = create_app(lifespan=None).openapi()

    for (path, method), model_name in SUCCESS_CONTRACTS.items():
        response = openapi["paths"][path][method]["responses"]["200"]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model_name}"
        }
        component = openapi["components"]["schemas"][model_name]
        assert component["type"] == "object"
        assert component["properties"]


def test_remaining_json_routes_declare_detail_error_envelopes() -> None:
    paths = create_app(lifespan=None).openapi()["paths"]

    for (path, method), status_codes in ERROR_CONTRACTS.items():
        responses = paths[path][method]["responses"]
        assert status_codes <= responses.keys()
        for status_code in status_codes:
            assert responses[status_code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }


def test_typed_data_envelopes_do_not_fall_back_to_free_form_objects() -> None:
    schemas = create_app(lifespan=None).openapi()["components"]["schemas"]

    assert schemas["ServerActionResponse"]["properties"]["data"] == {
        "$ref": "#/components/schemas/ServerActionStatusData"
    }
    assert schemas["CustomCommandExecutionResponse"]["properties"]["data"] == {
        "$ref": "#/components/schemas/CustomCommandResult"
    }
    command_results = schemas["CustomCommandResult"]["properties"]["results"]
    assert command_results["items"] == {
        "$ref": "#/components/schemas/CustomCommandResultEntry"
    }


@pytest.mark.asyncio
async def test_deployment_progress_response_body_remains_unchanged(monkeypatch) -> None:
    progress = [
        {
            "type": "output",
            "message": "downloading",
            "timestamp": "2026-07-25T12:00:00+08:00",
            "sequence": 4,
        }
    ]
    monkeypatch.setattr(
        deployment_routes,
        "get_server_and_verify_ownership",
        AsyncMock(return_value=SimpleNamespace(id=17)),
    )
    monkeypatch.setattr(
        deployment_routes.redis_manager,
        "get_deployment_progress",
        AsyncMock(return_value=progress),
    )

    app = FastAPI()
    app.include_router(deployment_routes.router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=7,
        is_admin=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/servers/17/deployment-progress")

    assert response.status_code == 200
    assert response.json() == {
        "server_id": 17,
        "progress_messages": progress,
        "total_messages": 1,
    }


@pytest.mark.asyncio
async def test_discord_probe_response_body_remains_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        configuration_routes,
        "get_server_with_permission",
        AsyncMock(return_value=SimpleNamespace(id=17)),
    )
    monkeypatch.setattr(
        configuration_routes.discord_notification_service,
        "send_test",
        AsyncMock(return_value=(True, "Discord notification sent")),
    )

    app = FastAPI()
    app.include_router(configuration_routes.router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=7,
        is_admin=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/servers/17/discord-settings/test",
            json={"message": "probe"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "Discord notification sent",
    }


def test_response_models_preserve_representative_wire_values() -> None:
    samples = (
        (
            OperationMessageResponse,
            {"success": True, "message": "done"},
        ),
        (
            SSHConnectionInfoResponse,
            {
                "connected": False,
                "created_at": None,
                "last_used": None,
                "connection_age": None,
                "idle_time": None,
                "in_use": False,
                "reconnection_count": 0,
                "max_reconnections": 5,
                "pooling_enabled": True,
                "connection_key": "server:17",
            },
        ),
        (
            DeploymentLockResponse,
            {"lock_exists": True, "server_status": "deploying"},
        ),
        (
            DeploymentProgressResponse,
            {
                "server_id": 17,
                "progress_messages": [
                    {
                        "type": "complete",
                        "message": "done",
                        "timestamp": "2026-07-25T12:00:00+08:00",
                    }
                ],
                "total_messages": 1,
            },
        ),
        (
            ServerActionResponse,
            {
                "success": True,
                "message": "started",
                "data": {"status": "running"},
            },
        ),
        (
            CustomCommandExecutionResponse,
            {
                "success": True,
                "message": "Executed 1 command(s) successfully",
                "data": {
                    "success": True,
                    "message": "Executed 1 command(s) successfully",
                    "target": "host",
                    "results": [
                        {
                            "index": 1,
                            "command": "uptime",
                            "success": True,
                            "stdout": "up",
                            "stderr": "",
                        }
                    ],
                },
            },
        ),
        (
            CustomCommandDeleteResponse,
            {
                "success": True,
                "message": "Custom command deleted successfully",
                "data": None,
            },
        ),
        (
            AllServersDiskSpaceResponse,
            {
                "servers": {
                    "17": {
                        "used_gb": 1.0,
                        "total_gb": 10.0,
                        "available_gb": 9.0,
                        "used_percent": 10.0,
                    },
                    "18": None,
                },
                "timestamp": "2026-07-25T12:00:00+08:00",
            },
        ),
        (
            DeploymentConfirmationResponse,
            {
                "success": True,
                "message": "Deployment marked as complete",
                "status": "stopped",
                "last_deployed": "2026-07-25T12:00:00+08:00",
            },
        ),
        (
            SSHReconnectResponse,
            {
                "success": False,
                "message": "offline",
                "ssh_health_status": "completely_down",
            },
        ),
        (
            SSHHealthResponse,
            {
                "server_id": 17,
                "ssh_health_status": "unhealthy",
                "consecutive_failures": 3,
                "failure_threshold": 84,
                "is_ssh_down": True,
                "last_ssh_success": None,
                "last_ssh_failure": "2026-07-25T11:00:00+08:00",
                "last_health_check": "2026-07-25T12:00:00+08:00",
                "check_interval_hours": 2,
                "offline_duration_estimate": {
                    "hours": 6,
                    "days": 0.2,
                    "description": "~6 hours (0.2 days)",
                },
                "monitoring_enabled": True,
            },
        ),
        (
            StartupCommandResponse,
            {
                "startup_command": "tmux new-session",
                "cs2_command": "./cs2 -dedicated",
                "session_manager": "tmux",
                "game_mode_resolved": "competitive (game_type: 0, game_mode: 1)",
            },
        ),
    )

    for model, payload in samples:
        assert model.model_validate(payload).model_dump(mode="json") == payload
