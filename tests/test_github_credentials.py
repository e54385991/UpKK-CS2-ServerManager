"""Tests for personal/global GitHub credential precedence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from modules.models import SystemSettings
from modules.schemas import SystemSettingsResponse
from services.github_credentials import get_effective_github_token


def test_system_settings_response_exposes_only_masked_token_status():
    settings = SystemSettings(
        id=1,
        global_github_token="github_pat_secret123456",
    )

    response = SystemSettingsResponse.model_validate(settings).model_dump()

    assert response["has_global_github_token"] is True
    assert response["global_github_token_prefix"] == "github_pat_s..."
    assert "global_github_token" not in response


@pytest.mark.asyncio
async def test_personal_github_token_takes_precedence():
    user = SimpleNamespace(github_token="  ghp_personal123  ")

    with patch(
        "services.github_credentials.SystemSettings.get_settings",
        new=AsyncMock(),
    ) as get_settings:
        token = await get_effective_github_token(object(), user)

    assert token == "ghp_personal123"
    get_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_github_token_is_used_only_as_fallback():
    user = SimpleNamespace(github_token=None)
    settings = SimpleNamespace(global_github_token="  github_pat_global123  ")

    with patch(
        "services.github_credentials.SystemSettings.get_settings",
        new=AsyncMock(return_value=settings),
    ):
        token = await get_effective_github_token(object(), user)

    assert token == "github_pat_global123"


@pytest.mark.asyncio
async def test_blank_tokens_resolve_to_none():
    user = SimpleNamespace(github_token="  ")
    settings = SimpleNamespace(global_github_token="")

    with patch(
        "services.github_credentials.SystemSettings.get_settings",
        new=AsyncMock(return_value=settings),
    ):
        token = await get_effective_github_token(object(), user)

    assert token is None
