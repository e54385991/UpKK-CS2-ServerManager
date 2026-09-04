"""覆盖 Discord 管理器的纯格式化、权限和确认计划辅助函数。"""

from __future__ import annotations

import hashlib
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import discord_bot_manager as manager_module
from services.ai_security import encrypt_credential
from services.change_map_service import MapCandidate


def _server():
    return SimpleNamespace(
        id=3,
        name="server",
        host="host",
        game_port=27015,
        a2s_query_host=None,
        a2s_query_port=None,
        discord_crash_restart_min_interval_minutes=10,
    )


def test_discord_helpers_status_permissions_and_plans(monkeypatch):
    assert manager_module._safe_text({"token": "secret"}).find("secret") == -1
    assert manager_module._member_roles(
        SimpleNamespace(roles=[SimpleNamespace(id=1), SimpleNamespace(id=None)])
    ) == {"1"}
    assert manager_module._has_channel_manage_permission(None) is False
    assert manager_module._has_channel_manage_permission(SimpleNamespace(administrator=True))
    assert manager_module._has_administrator_permission(SimpleNamespace(administrator=True))
    owner = SimpleNamespace(guild=SimpleNamespace(owner_id=7), user=SimpleNamespace(id=7))
    assert manager_module._is_guild_owner(owner)
    assert manager_module._actor_privileges(owner) == (True, True)
    message = SimpleNamespace(raw_mentions=[3], mentions=[])
    assert manager_module._message_mentions_bot(message, 3)
    assert not manager_module._message_mentions_bot(
        SimpleNamespace(raw_mentions=[], mentions=[]), 3
    )
    monkeypatch.setattr(
        manager_module, "get_current_time", lambda: datetime(2025, 1, 1, tzinfo=timezone.utc)
    )
    assert manager_module.format_panel_update_age(None) is None
    assert manager_module.format_panel_update_age("bad") is None
    assert manager_module.format_panel_update_age("2024-12-31T23:59:30+00:00") == "30s ago"
    assert manager_module.format_panel_update_age("2024-12-31T23:00:00+00:00") == "1h ago"
    assert manager_module.format_panel_update_age("2025-01-02T00:00:00+00:00").startswith("2025")
    fields = manager_module.status_card_fields(
        _server(),
        a2s_ok=False,
        info=None,
        response_time_ms=-1,
        last_updated="bad",
        disk_info={"used_gb": 1.2, "total_gb": 10, "used_percent": 12},
    )
    assert len(fields) == 10 and "GB" in fields[7][1]
    assert manager_module._format_disk_gb(True, "en-US") == manager_module._status_unknown("en-US")
    assert manager_module._format_latency_ms(-4, "en-US") == "0ms"
    simple = manager_module._simple_plan(_server(), "restart")
    console = manager_module._game_console_plan(_server(), "status")
    assert (
        simple["action"] == "restart"
        and console["command_hash"] == hashlib.sha256(b"status").hexdigest()
    )
    candidate = MapCandidate(name="Workshop", workshop_id="123456")
    map_plan = manager_module._change_map_plan(_server(), candidate)
    assert map_plan["command"] == "host_workshop_map 123456"


@pytest.mark.asyncio
async def test_discord_operation_argument_validation_and_status_sources(monkeypatch):
    command = "status"
    item = SimpleNamespace(
        arguments={
            "command_encrypted": encrypt_credential(command),
            "command_hash": hashlib.sha256(command.encode()).hexdigest(),
        }
    )
    assert manager_module._operation_game_console_command(item) == command
    with pytest.raises(manager_module.DiscordOperationDenied):
        manager_module._operation_game_console_command(SimpleNamespace(arguments={}))
    map_command = "host_workshop_map 123456"
    map_item = SimpleNamespace(
        arguments={
            "command_encrypted": encrypt_credential(map_command),
            "command_hash": hashlib.sha256(map_command.encode()).hexdigest(),
            "name": "Workshop",
            "workshop_id": "123456",
            "filename": "",
        }
    )
    candidate = manager_module._operation_change_map_candidate(map_item)
    assert candidate.command == map_command
    bad_item = SimpleNamespace(
        arguments={**map_item.arguments, "command_hash": hashlib.sha256(b"map bad").hexdigest()}
    )
    with pytest.raises(manager_module.DiscordOperationDenied):
        manager_module._operation_change_map_candidate(bad_item)
    cached = {
        "success": True,
        "server_info": {"server_name": "cached"},
        "response_time_ms": 4,
        "last_updated": "now",
    }
    from services import a2s_cache_service

    disk_module = importlib.import_module("services.disk_space_service")

    monkeypatch.setattr(a2s_cache_service, "get_cached_info", AsyncMock(return_value=cached))
    monkeypatch.setattr(
        disk_module.disk_space_service,
        "get_disk_space",
        AsyncMock(return_value=(True, {"used_gb": 1})),
    )
    sources = await manager_module.load_panel_status_sources(_server())
    assert sources["a2s_ok"] and sources["disk_info"]
    monkeypatch.setattr(a2s_cache_service, "get_cached_info", AsyncMock(return_value=None))
    from services import a2s_query

    monkeypatch.setattr(
        a2s_query.a2s_service, "query_server_info", AsyncMock(return_value=(False, None))
    )
    sources = await manager_module.load_panel_status_sources(_server())
    assert not sources["a2s_ok"]
