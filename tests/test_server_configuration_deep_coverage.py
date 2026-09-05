"""覆盖服务器配置、Discord 设置、快捷命令和启动命令预览。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.servers import configuration as config
from modules import (
    CustomCommandCreate,
    CustomCommandExecuteRequest,
    CustomCommandUpdate,
    DiscordSettingsUpdate,
)


class _Db:
    def __init__(self):
        self.added = []
        self.deleted = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None


class _Command:
    def __init__(self, **values):
        self.__dict__.update(values)

    def sqlmodel_update(self, values):
        self.__dict__.update(values)


def _server(**overrides):
    values = dict(
        id=3,
        user_id=7,
        default_map="de_dust2",
        game_mode="competitive",
        game_type="0",
        additional_parameters="",
        max_players=16,
        server_name="Alpha",
        game_port=27015,
        ip_address=None,
        client_port=None,
        steam_account_token="GSLT",
        server_password="pw",
        rcon_password="rcon",
        tv_enable=True,
        tv_port=27020,
        cpu_affinity="0, 1",
        api_key="api-key",
        backend_url="https://backend.invalid",
        game_directory="/srv/cs2",
        session_manager="tmux",
        discord_webhook_url="https://discord.com/api/webhooks/1/x",
        discord_notifications_enabled=False,
        discord_notify_auto_updates=False,
        discord_notify_manual_updates=False,
        discord_notify_plugin_updates=False,
        discord_notify_s3_backups=False,
        discord_notify_crash_restarts=False,
        discord_channel_name=None,
        discord_crash_restart_min_interval_minutes=10,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_discord_settings_routes_validate_update_and_test_notification(monkeypatch):
    server = _server()
    db = _Db()
    user = SimpleNamespace(id=7)
    monkeypatch.setattr(config, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(
        config.discord_notification_service,
        "webhook_configured",
        lambda value: bool(value.discord_webhook_url),
    )
    response = await config.get_discord_settings(3, db, user)
    assert response.webhook_configured is True
    monkeypatch.setattr(
        config.discord_notification_service,
        "validate_webhook_url",
        lambda _url: (False, "bad webhook"),
    )
    with pytest.raises(HTTPException, match="bad webhook"):
        await config.update_discord_settings(
            3, DiscordSettingsUpdate(discord_webhook_url="https://bad"), db, user, SimpleNamespace()
        )
    with pytest.raises(HTTPException, match="required"):
        await config.update_discord_settings(
            3,
            DiscordSettingsUpdate(discord_notifications_enabled=True, clear_webhook=True),
            db,
            user,
            SimpleNamespace(),
        )

    monkeypatch.setattr(
        config.discord_notification_service, "validate_webhook_url", lambda _url: (True, "")
    )
    monkeypatch.setattr(config.redis_manager, "clear_server_cache", AsyncMock())
    monkeypatch.setattr(config, "record_audit_event", AsyncMock())
    updated = await config.update_discord_settings(
        3,
        DiscordSettingsUpdate(
            discord_webhook_url="https://discord.com/api/webhooks/2/y",
            discord_notifications_enabled=True,
            discord_channel_name="  ops ",
            discord_crash_restart_min_interval_minutes=None,
        ),
        db,
        user,
        SimpleNamespace(),
    )
    assert updated.webhook_configured and server.discord_channel_name == "ops"
    assert server.discord_crash_restart_min_interval_minutes == 10
    assert db.commits == 1

    monkeypatch.setattr(
        config.discord_notification_service,
        "send_test",
        AsyncMock(return_value=(False, "not sent")),
    )
    with pytest.raises(HTTPException, match="not sent"):
        await config.test_discord_settings(3, SimpleNamespace(message="hello"), db, user)
    monkeypatch.setattr(
        config.discord_notification_service, "send_test", AsyncMock(return_value=(True, "sent"))
    )
    assert (await config.test_discord_settings(3, SimpleNamespace(message="hello"), db, user))[
        "success"
    ] is True


@pytest.mark.asyncio
async def test_custom_command_routes_and_audit_mapping(monkeypatch):
    server = _server()
    db = _Db()
    user = SimpleNamespace(id=7)
    monkeypatch.setattr(config, "get_server_with_permission", AsyncMock(return_value=server))
    command = _Command(id=2, name="status", target="host", commands="uptime")
    monkeypatch.setattr(
        config.CustomCommand, "get_all_by_server_and_user", AsyncMock(return_value=[command])
    )
    assert await config.list_custom_commands(3, db, user) == [command]
    created = await config.create_custom_command(
        3, CustomCommandCreate(name="status", target="host", commands="uptime"), db, user
    )
    assert created.name == "status" and db.commits == 1
    monkeypatch.setattr(
        config.CustomCommand, "get_by_id_server_and_user", AsyncMock(return_value=command)
    )
    changed = await config.update_custom_command(3, 2, CustomCommandUpdate(name="new"), db, user)
    assert changed.name == "new"
    assert (await config.delete_custom_command(3, 2, db, user)).success is True
    monkeypatch.setattr(
        config,
        "execute_and_log_custom_commands",
        AsyncMock(return_value={"success": True, "message": "ok", "results": []}),
    )
    monkeypatch.setattr(config, "record_audit_event", AsyncMock())
    result = await config.execute_one_time_custom_command(
        3,
        CustomCommandExecuteRequest(target="host", commands="uptime"),
        db,
        user,
        SimpleNamespace(),
    )
    assert result.success is True
    result = await config.execute_saved_custom_command(3, 2, db, user, SimpleNamespace())
    assert result.success is True
    monkeypatch.setattr(
        config.CustomCommand, "get_by_id_server_and_user", AsyncMock(return_value=None)
    )
    with pytest.raises(HTTPException, match="not found"):
        await config.update_custom_command(3, 2, CustomCommandUpdate(name="x"), db, user)


@pytest.mark.asyncio
async def test_startup_command_preview_covers_api_key_and_shell_fallback(monkeypatch):
    user = SimpleNamespace(id=7)
    db = _Db()
    monkeypatch.setattr(config, "get_server_with_permission", AsyncMock(return_value=_server()))
    result = await config.get_startup_command(3, db, user)
    assert "***API_KEY***" in result["startup_command"]
    assert "***PASSWORD***" in result["cs2_command"]
    assert "taskset" in result["cs2_command"]

    shell_server = _server(
        api_key=None,
        cpu_affinity="bad;command",
        ip_address="127.0.0.1",
        client_port=27030,
        tv_enable=False,
        steam_account_token=None,
        server_password=None,
        rcon_password=None,
        additional_parameters="-tickrate 128",
    )
    monkeypatch.setattr(config, "get_server_with_permission", AsyncMock(return_value=shell_server))
    result = await config.get_startup_command(3, db, user)
    assert "console.log" in result["startup_command"]
    assert "clientport 27030" in result["cs2_command"]

    invalid = _server(default_map="bad map")
    monkeypatch.setattr(config, "get_server_with_permission", AsyncMock(return_value=invalid))
    with pytest.raises(HTTPException) as exc_info:
        await config.get_startup_command(3, db, user)
    assert exc_info.value.status_code == 422
