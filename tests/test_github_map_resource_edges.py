from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import github_plugins, map_management
from modules.http_helper import http_helper


@pytest.mark.parametrize(
    "coerce",
    (
        github_plugins._coerce_ssh_manager,
        map_management._coerce_ssh_manager,
    ),
)
def test_remote_routes_reject_invalid_ssh_dependency_resources(coerce) -> None:
    with pytest.raises(HTTPException) as exc_info:
        coerce(object())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "SSH connection pool is unavailable"


def test_github_route_rejects_invalid_http_dependency_resource() -> None:
    with pytest.raises(HTTPException) as exc_info:
        github_plugins._coerce_application_http(object())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Outbound HTTP client is unavailable"


@pytest.mark.asyncio
async def test_github_server_lookup_hides_resources_for_user_without_identity() -> None:
    database = SimpleNamespace(commit=AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await github_plugins.get_server_and_verify_ownership(
            database,
            17,
            SimpleNamespace(id=None, is_admin=False),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Server not found"
    database.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_github_release_call_keeps_legacy_http_facade(monkeypatch) -> None:
    database = SimpleNamespace(commit=AsyncMock())
    outbound_get = AsyncMock(return_value=(True, [], None))
    monkeypatch.setattr(http_helper, "get", outbound_get)
    monkeypatch.setattr(
        github_plugins,
        "get_effective_github_token",
        AsyncMock(return_value=None),
    )

    response = await github_plugins.get_github_releases(
        "https://github.com/acme/plugin",
        db=database,
        current_user=SimpleNamespace(id=7, is_admin=False),
    )

    assert response.success is True
    assert response.repo_owner == "acme"
    assert response.repo_name == "plugin"
    assert response.releases == []
    database.commit.assert_awaited_once()
    outbound_get.assert_awaited_once()
