"""Lifecycle-managed per-user Discord Gateway clients and Slash Commands."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

import discord
from discord import app_commands
from sqlalchemy import func
from sqlmodel import col, select

from modules.database import async_session_maker
from modules.models import (
    DiscordOperationRun,
    ManagedPlugin,
    MarketPlugin,
    Server,
    ServerDiscordBinding,
    User,
    UserDiscordBot,
)
from modules.schemas.discord import DiscordCapability
from modules.utils import get_current_time
from services.ai_security import decrypt_credential, encrypt_credential, redact_sensitive_text
from services.ai_tools import (
    TOOLS_BY_NAME,
    ApplyPluginPlanInput,
    GameConsoleCommandInput,
    ServerControlInput,
    ServerOperationInput,
    ToolContext,
)
from services.audit_log_service import record_discord_operation_event
from services.change_map_service import (
    ChangeMapAmbiguousError,
    ChangeMapError,
    MapCandidate,
    candidate_from_map,
    load_map_pool,
    resolve_change_map,
)
from services.discord.access import DiscordAccessMixin
from services.discord.client import ManagedDiscordClient, _AIConfirmView, _ConfirmView, _Runtime
from services.discord.commands import register_commands
from services.discord.components import DiscordComponentsMixin
from services.discord.interaction import (
    _edit_interaction_message,
    _edit_webhook_message,
    _is_unknown_discord_resource,
    _public_error_text,
    _publish_interaction_update,
    _safe_text,
)
from services.discord.lifecycle import DiscordLifecycleMixin
from services.discord.menu import DiscordMenuMixin
from services.discord.permissions import (
    _actor_privileges,
    _has_administrator_permission,
    _has_channel_manage_permission,
    _is_channel_manager,
    _is_guild_owner,
    _is_server_administrator,
    _member_roles,
    _message_mentions_bot,
    _roles,
)
from services.discord.plans import (
    _change_map_plan,
    _game_console_plan,
    _operation_change_map_candidate,
    _operation_game_console_command,
    _simple_plan,
)
from services.discord.slash import DiscordSlashMixin
from services.discord.status import (
    _format_disk_gb,
    _format_disk_percent,
    _format_latency_ms,
    _real_status_text,
    _status_unknown,
    format_panel_update_age,
    load_panel_status_sources,
    status_card_fields,
)
from services.discord_ai_service import (
    approve_discord_tool,
    ask_discord_agent,
    available_discord_agent_server_ids,
    discord_run_snapshot,
    reset_discord_conversation,
    resolve_discord_agent_server,
)
from services.discord_authorization_service import (
    DiscordAuthorizationDenied,
    authorized_bindings,
)
from services.discord_bot_service import DISCORD_COMMAND_CHANNEL_TYPES
from services.discord_menu_ui import (
    MENU_LIFETIME_SECONDS,
    PLUGIN_PAGE_SIZE,
    MenuInputModal,
    action_capability,
    control_view,
    is_exact_wake_word,
    launcher_is_expired,
    mention_trigger_content,
    menu_is_expired,
    menu_issued_at,
    no_access_view,
    plugin_picker_view,
    server_picker_view,
)
from services.discord_menu_ui import (
    text as menu_text,
)
from services.discord_operation_service import (
    DiscordOperationDenied,
    confirm_operation,
    create_operation,
)
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)
LEASE_TTL_SECONDS = 60
LEASE_RENEW_SECONDS = 20
RECONCILE_SECONDS = 30
MESSAGE_TRIGGER_MENTION_ONLY = "mention_only"
MESSAGE_TRIGGER_GREETINGS = "mention_and_greetings"
# Discord rejects channel-token edits of interaction/webhook messages with 10008.
UNKNOWN_DISCORD_RESOURCE_CODES = {10008, 10015, 10062}


class DiscordBotManager(
    DiscordLifecycleMixin,
    DiscordAccessMixin,
    DiscordSlashMixin,
    DiscordMenuMixin,
    DiscordComponentsMixin,
):
    def __init__(self) -> None:
        self._runtimes: dict[int, _Runtime] = {}
        self._started = False
        self._reconcile_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()


discord_bot_manager = DiscordBotManager()

_PATCHABLE = (
    ApplyPluginPlanInput,
    ChangeMapAmbiguousError,
    ChangeMapError,
    DISCORD_COMMAND_CHANNEL_TYPES,
    DiscordAuthorizationDenied,
    DiscordCapability,
    DiscordOperationDenied,
    DiscordOperationRun,
    GameConsoleCommandInput,
    MENU_LIFETIME_SECONDS,
    ManagedPlugin,
    MapCandidate,
    MarketPlugin,
    MenuInputModal,
    PLUGIN_PAGE_SIZE,
    Server,
    ServerControlInput,
    ServerDiscordBinding,
    ServerOperationInput,
    TOOLS_BY_NAME,
    ToolContext,
    User,
    UserDiscordBot,
    _AIConfirmView,
    _ConfirmView,
    _Runtime,
    _actor_privileges,
    _change_map_plan,
    _edit_interaction_message,
    _edit_webhook_message,
    _format_disk_gb,
    _format_disk_percent,
    _format_latency_ms,
    _game_console_plan,
    _has_administrator_permission,
    _has_channel_manage_permission,
    _is_channel_manager,
    _is_guild_owner,
    _is_server_administrator,
    _is_unknown_discord_resource,
    _member_roles,
    _message_mentions_bot,
    _operation_change_map_candidate,
    _operation_game_console_command,
    _public_error_text,
    _publish_interaction_update,
    _real_status_text,
    _roles,
    _safe_text,
    _simple_plan,
    _status_unknown,
    action_capability,
    approve_discord_tool,
    ask_discord_agent,
    async_session_maker,
    asyncio,
    authorized_bindings,
    available_discord_agent_server_ids,
    candidate_from_map,
    col,
    confirm_operation,
    control_view,
    create_operation,
    decrypt_credential,
    discord_run_snapshot,
    encrypt_credential,
    format_panel_update_age,
    func,
    get_current_time,
    hashlib,
    inspect,
    is_exact_wake_word,
    json,
    launcher_is_expired,
    load_map_pool,
    load_panel_status_sources,
    logger,
    math,
    mention_trigger_content,
    menu_is_expired,
    menu_issued_at,
    menu_text,
    no_access_view,
    plugin_picker_view,
    record_discord_operation_event,
    redact_sensitive_text,
    redis_manager,
    register_commands,
    reset_discord_conversation,
    resolve_change_map,
    resolve_discord_agent_server,
    select,
    server_picker_view,
    status_card_fields,
    uuid,
)

# Re-exported for tests that still construct views/clients from this module.
_KEEP = (
    ManagedDiscordClient,
    discord,
    app_commands,
    datetime,
    dataclass,
    suppress,
)
