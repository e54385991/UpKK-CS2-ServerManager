"""Discord Gateway Bot and server-level AI authorization regressions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from api.routes.discord_bot import (
    update_discord_global_binding,
)
from modules.models import (
    AuthType,
    Server,
    ServerDiscordBinding,
)
from modules.schemas.discord import (
    DiscordCapability,
    DiscordGlobalBindingResponse,
    DiscordGlobalBindingUpdate,
)
from services.discord_binding_template_service import (
    binding_matches_template,
    inherit_global_discord_binding,
    sync_global_discord_binding,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from tests.discord_bot_agent_policy_fakes import (  # noqa: E402 - preserves initialization and registration order
    _auth_fixtures as _auth_fixtures,
)
from tests.discord_bot_agent_policy_fakes import (  # noqa: E402 - preserves initialization and registration order
    _discord_http_error as _discord_http_error,
)
from tests.discord_bot_agent_policy_fakes import (  # noqa: E402 - preserves initialization and registration order
    _global_bot as _global_bot,
)


@pytest.mark.asyncio
async def test_new_server_inherits_global_discord_template_without_committing():
    bot = _global_bot()
    server = Server(
        id=10,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    db = AsyncMock()
    db.add = Mock()
    db.get.side_effect = [bot, None]

    binding = await inherit_global_discord_binding(db, server)

    assert binding is not None
    assert binding.enabled is True
    assert binding.guild_id == "100"
    assert binding.channel_ids == ["200"]
    assert binding.capabilities == ["status", "restart"]
    assert binding.allow_channel_managers is False
    assert binding.allow_server_administrators is False
    assert binding_matches_template(binding, bot) is True
    db.add.assert_called_once_with(binding)
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_server_inherits_explicit_channel_manager_switch():
    bot = _global_bot()
    bot.global_allow_channel_managers = True
    server = Server(
        id=12,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    db = AsyncMock()
    db.add = Mock()
    db.get.side_effect = [bot, None]

    binding = await inherit_global_discord_binding(db, server)

    assert binding is not None
    assert binding.allow_channel_managers is True
    assert binding.allow_server_administrators is False
    assert binding_matches_template(binding, bot) is True


@pytest.mark.asyncio
async def test_new_server_inherits_explicit_server_administrator_switch():
    bot = _global_bot()
    bot.global_allow_server_administrators = True
    server = Server(
        id=13,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    db = AsyncMock()
    db.add = Mock()
    db.get.side_effect = [bot, None]

    binding = await inherit_global_discord_binding(db, server)

    assert binding is not None
    assert binding.allow_server_administrators is True
    assert binding.allow_channel_managers is False
    assert binding_matches_template(binding, bot) is True


@pytest.mark.asyncio
async def test_explicit_global_sync_overwrites_every_owned_server_binding():
    bot = _global_bot()
    servers = [SimpleNamespace(id=10), SimpleNamespace(id=11)]
    customized = ServerDiscordBinding(
        server_id=10,
        user_id=1,
        enabled=False,
        guild_id="999",
        channel_ids=["998"],
        role_ids=["997"],
        capabilities=["stop"],
    )
    server_result = Mock()
    server_result.scalars.return_value.all.return_value = servers
    binding_result = Mock()
    binding_result.scalars.return_value.all.return_value = [customized]
    db = AsyncMock()
    db.add = Mock()
    db.execute.side_effect = [server_result, binding_result]

    count = await sync_global_discord_binding(db, bot)

    assert count == 2
    assert binding_matches_template(customized, bot) is True
    created = next(call.args[0] for call in db.add.call_args_list if call.args[0].server_id == 11)
    assert binding_matches_template(created, bot) is True
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_api_syncs_only_when_explicitly_requested(monkeypatch):
    bot = _global_bot()
    bot.global_binding_configured = False
    monkeypatch.setattr(
        "api.routes.discord_bot._stored_token", AsyncMock(return_value=(bot, "secret-token"))
    )
    monkeypatch.setattr(
        "api.routes.discord_bot._validate_binding_selection", AsyncMock(return_value=None)
    )
    sync = AsyncMock(return_value=3)
    monkeypatch.setattr("api.routes.discord_bot.sync_global_discord_binding", sync)
    response = DiscordGlobalBindingResponse(
        configured=True,
        enabled=True,
        guild_id="100",
        channel_ids=["200"],
        role_ids=["300"],
        capabilities=[DiscordCapability.STATUS],
        server_count=3,
        matching_server_count=3,
        synced_server_count=3,
    )
    monkeypatch.setattr(
        "api.routes.discord_bot._global_binding_response", AsyncMock(return_value=response)
    )
    notify = AsyncMock()
    monkeypatch.setattr("api.routes.discord_bot._notify_manager", notify)
    db = AsyncMock()
    db.add = Mock()

    result = await update_discord_global_binding(
        DiscordGlobalBindingUpdate(
            enabled=True,
            guild_id="100",
            channel_ids=["200"],
            role_ids=["300"],
            capabilities=[DiscordCapability.STATUS],
            sync_existing_servers=True,
        ),
        db,
        SimpleNamespace(id=1),
        MagicMock(),
    )

    assert result.synced_server_count == 3
    assert bot.global_binding_configured is True
    assert bot.global_channel_ids == ["200"]
    sync.assert_awaited_once_with(db, bot)
    db.commit.assert_awaited_once()
    notify.assert_awaited_once_with(1)

    sync.reset_mock()
    notify.reset_mock()
    await update_discord_global_binding(
        DiscordGlobalBindingUpdate(
            enabled=True,
            guild_id="100",
            channel_ids=["200"],
            role_ids=["300"],
            capabilities=[DiscordCapability.STATUS],
            sync_existing_servers=False,
        ),
        db,
        SimpleNamespace(id=1),
        MagicMock(),
    )
    sync.assert_not_awaited()
    notify.assert_not_awaited()
