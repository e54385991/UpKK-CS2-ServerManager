"""Coverage for the administrator-controlled CAPTCHA policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.captcha_policy import require_captcha


@pytest.mark.asyncio
async def test_disabled_policy_skips_captcha_validation(monkeypatch):
    monkeypatch.setattr(
        "services.captcha_policy.SystemSettings.get_settings",
        AsyncMock(return_value=SimpleNamespace(captcha_enabled=False)),
    )
    validate = AsyncMock()
    monkeypatch.setattr("services.captcha_policy.captcha_service.validate_captcha", validate)

    await require_captcha(SimpleNamespace(execute=AsyncMock()), None, None)

    validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_policy_requires_and_validates_captcha(monkeypatch):
    monkeypatch.setattr(
        "services.captcha_policy.SystemSettings.get_settings",
        AsyncMock(return_value=SimpleNamespace(captcha_enabled=True)),
    )
    validate = AsyncMock(return_value=True)
    monkeypatch.setattr("services.captcha_policy.captcha_service.validate_captcha", validate)

    with pytest.raises(HTTPException, match="CAPTCHA is required"):
        await require_captcha(SimpleNamespace(execute=AsyncMock()), None, None)
    await require_captcha(SimpleNamespace(execute=AsyncMock()), "token", "ABCD")

    validate.assert_awaited_once_with("token", "ABCD")
