"""Focused coverage for SQLModel partial-update route behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from api.routes import plugin_auto_update, system_settings
from modules import ManagedPlugin, ManagedPluginUpdate, SystemSettings, SystemSettingsUpdate


def _database_session():
    return SimpleNamespace(add=Mock(), commit=AsyncMock(), refresh=AsyncMock())


@pytest.mark.asyncio
async def test_managed_plugin_update_preserves_omitted_fields_and_applies_explicit_null(
    monkeypatch,
):
    plugin = ManagedPlugin(
        id=11,
        server_id=7,
        source_type="github",
        source_key="example/plugin",
        display_name="Example Plugin",
        custom_install_path="addons/example",
        auto_update_enabled=False,
    )
    db = _database_session()
    monkeypatch.setattr(plugin_auto_update, "owned_server", AsyncMock())
    monkeypatch.setattr(plugin_auto_update, "owned_plugin", AsyncMock(return_value=plugin))

    response = await plugin_auto_update.update_plugin(
        server_id=7,
        plugin_id=11,
        request=ManagedPluginUpdate(custom_install_path=None, auto_update_enabled=True),
        db=db,
        current_user=SimpleNamespace(id=3),
    )

    assert plugin.display_name == "Example Plugin"
    assert plugin.custom_install_path is None
    assert plugin.auto_update_enabled is True
    assert response.custom_install_path is None
    db.add.assert_called_once_with(plugin)
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(plugin)


@pytest.mark.asyncio
async def test_system_settings_update_keeps_protected_token_without_clear_flag(monkeypatch):
    settings = SystemSettings(
        global_github_token="github_pat_existing_token",
        email_from_name="Old Name",
        smtp_host="smtp.example.com",
    )
    db = _database_session()
    monkeypatch.setattr(
        system_settings.SystemSettings,
        "get_or_create_settings",
        AsyncMock(return_value=settings),
    )

    result = await system_settings.update_system_settings(
        settings_update=SystemSettingsUpdate(
            email_from_name="New Name",
            global_github_token=None,
        ),
        db=db,
        current_user=SimpleNamespace(is_admin=True),
        request=MagicMock(),
    )

    assert result is settings
    assert settings.email_from_name == "New Name"
    assert settings.smtp_host == "smtp.example.com"
    assert settings.global_github_token == "github_pat_existing_token"
    db.add.assert_called_once_with(settings)
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(settings)
