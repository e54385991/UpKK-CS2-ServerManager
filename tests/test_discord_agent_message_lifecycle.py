"""Discord Gateway Bot and server-level AI authorization regressions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from modules.models import (
    ServerDiscordBinding,
)
from services.discord_bot_manager import (
    DiscordBotManager,
    _edit_interaction_message,
    _is_unknown_discord_resource,
    _public_error_text,
    _publish_interaction_update,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from tests.discord_bot_agent_policy_fakes import (
    _auth_fixtures as _auth_fixtures,
)
from tests.discord_bot_agent_policy_fakes import (
    _discord_http_error as _discord_http_error,
)
from tests.discord_bot_agent_policy_fakes import (
    _global_bot as _global_bot,
)


@pytest.mark.asyncio
async def test_leading_mention_task_routes_to_available_discord_agent(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(
        owner_user_id=1,
        message_trigger_mode="mention_only",
        user=SimpleNamespace(id=999),
    )
    binding = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=True,
        capabilities=["agent_ask"],
    )
    server = SimpleNamespace(id=10, name="[UPKK] CS2 ZE #1")
    monkeypatch.setattr(
        manager, "_authorized_menu_pairs", AsyncMock(return_value=[(binding, server)])
    )
    monkeypatch.setattr(
        "services.discord_bot_manager.redis_manager.hit_rate_limit",
        AsyncMock(return_value=(True, 0)),
    )
    resolver = AsyncMock(return_value=server)
    runner = AsyncMock()
    monkeypatch.setattr(manager, "_message_agent_server", resolver)
    monkeypatch.setattr(manager, "_run_message_agent", runner)
    message = SimpleNamespace(
        guild=SimpleNamespace(id=100, preferred_locale="zh-CN"),
        channel=SimpleNamespace(id=200),
        author=SimpleNamespace(id=400, bot=False, roles=[]),
        webhook_id=None,
        raw_mentions=[999],
        content="<@999> ze1 发送游戏控制台命令 meta list 并回报结果",
        reply=AsyncMock(),
    )

    await manager.handle_message(client, message)

    prompt = "ze1 发送游戏控制台命令 meta list 并回报结果"
    resolver.assert_awaited_once_with(client, [(binding, server)], prompt)
    runner.assert_awaited_once_with(client, message, server, prompt)
    message.reply.assert_not_awaited()

    resolver.reset_mock()
    runner.reset_mock()
    message.content = "<@999> menu"
    await manager.handle_message(client, message)
    resolver.assert_not_awaited()
    runner.assert_not_awaited()
    message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_message_agent_runs_in_selected_server_context(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(owner_user_id=1)
    server = SimpleNamespace(id=10, name="[UPKK] CS2 ZE #1")
    progress_message = SimpleNamespace(edit=AsyncMock())
    message = SimpleNamespace(
        guild=SimpleNamespace(id=100),
        channel=SimpleNamespace(id=200),
        author=SimpleNamespace(id=400),
        reply=AsyncMock(return_value=progress_message),
    )
    ask = AsyncMock(return_value="run-1")
    render = AsyncMock(return_value=True)
    monkeypatch.setattr("services.discord_bot_manager.ask_discord_agent", ask)
    monkeypatch.setattr(manager, "_render_ai_run_message", render)

    await manager._run_message_agent(client, message, server, "ze1 meta list")

    ask.assert_awaited_once_with(
        owner_user_id=1,
        server_id=10,
        actor_user_id="400",
        guild_id="100",
        channel_id="200",
        prompt="ze1 meta list",
    )
    render.assert_awaited_once_with(progress_message, "run-1")
    assert message.reply.await_args.kwargs["silent"] is True
    assert message.reply.await_args.kwargs["mention_author"] is False


@pytest.mark.asyncio
async def test_message_agent_server_filters_discord_and_agent_authorization(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(owner_user_id=1)
    status_server = SimpleNamespace(id=10, name="[UPKK] CS2 KZ #1")
    agent_server = SimpleNamespace(id=11, name="[UPKK] CS2 ZE #1")
    pairs = [
        (
            ServerDiscordBinding(server_id=10, user_id=1, enabled=True, capabilities=["status"]),
            status_server,
        ),
        (
            ServerDiscordBinding(server_id=11, user_id=1, enabled=True, capabilities=["agent_ask"]),
            agent_server,
        ),
    ]
    captured = {}

    async def available(**kwargs):
        captured["owner_user_id"] = kwargs["owner_user_id"]
        captured["server_ids"] = list(kwargs["server_ids"])
        return frozenset({11})

    monkeypatch.setattr(
        "services.discord_bot_manager.available_discord_agent_server_ids", available
    )

    selected = await manager._message_agent_server(client, pairs, "ze1 meta list")

    assert selected is agent_server
    assert captured == {"owner_user_id": 1, "server_ids": [11]}


def test_unknown_message_errors_are_not_shown_verbatim():
    unknown = _discord_http_error(10008)
    assert _is_unknown_discord_resource(unknown)
    assert "10008" not in _public_error_text(unknown)
    assert "Unknown Message" not in _public_error_text(unknown)
    assert "no longer available" in _public_error_text(unknown)


@pytest.mark.asyncio
async def test_respond_error_hides_discord_unknown_message():
    manager = DiscordBotManager()
    interaction = SimpleNamespace(
        response=SimpleNamespace(is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await manager._respond_error(interaction, _discord_http_error(10008))

    sent = interaction.followup.send.await_args.args[0]
    assert "10008" not in sent
    assert "Unknown Message" not in sent


@pytest.mark.asyncio
async def test_edit_interaction_message_uses_original_response_after_defer():
    interaction = SimpleNamespace(
        response=SimpleNamespace(is_done=lambda: True, edit_message=AsyncMock()),
        edit_original_response=AsyncMock(),
        message=SimpleNamespace(edit=AsyncMock()),
    )

    assert await _edit_interaction_message(interaction, content="Running")
    interaction.edit_original_response.assert_awaited_once_with(content="Running")
    interaction.message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_interaction_update_falls_back_when_original_is_gone():
    interaction = SimpleNamespace(
        response=SimpleNamespace(is_done=lambda: True),
        edit_original_response=AsyncMock(side_effect=_discord_http_error(10008)),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    assert await _publish_interaction_update(
        interaction, embed=discord.Embed(title="completed"), view=None
    )
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is False


@pytest.mark.asyncio
async def test_message_launcher_ignores_deleted_trigger_message(monkeypatch):
    manager = DiscordBotManager()
    client = SimpleNamespace(
        owner_user_id=1,
        message_trigger_mode="mention_and_greetings",
        user=SimpleNamespace(id=999),
    )
    binding = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=True,
        capabilities=["status"],
    )
    server = SimpleNamespace(id=10, name="Server")
    monkeypatch.setattr(
        manager, "_authorized_menu_pairs", AsyncMock(return_value=[(binding, server)])
    )
    monkeypatch.setattr(
        "services.discord_bot_manager.redis_manager.hit_rate_limit",
        AsyncMock(return_value=(True, 0)),
    )
    message = SimpleNamespace(
        guild=SimpleNamespace(id=100, preferred_locale="zh-CN"),
        channel=SimpleNamespace(id=200),
        author=SimpleNamespace(id=400, bot=False, roles=[]),
        webhook_id=None,
        raw_mentions=[],
        content="你好！",
        reply=AsyncMock(side_effect=_discord_http_error(10008)),
    )

    await manager.handle_message(client, message)

    message.reply.assert_awaited_once()
