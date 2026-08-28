"""Localized Discord Components V2 layouts for the friendly CS2 control menu."""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable

import discord

from modules.schemas.discord import DiscordCapability

MENU_LIFETIME_SECONDS = 15 * 60
LAUNCHER_LIFETIME_SECONDS = 5 * 60
SERVER_PAGE_SIZE = 20
PLUGIN_PAGE_SIZE = 20

_WAKE_WORDS = frozenset({"你好", "您好", "hello", "菜单", "menu"})
_TRAILING_PUNCTUATION = " .,!?:;。！？，：；、~～"

_TEXT = {
    "en": {
        "launcher_title": "🎮 CS2 Server Control",
        "launcher_body": "Open a private control menu. Servers and actions are filtered to your current authorization.",
        "open": "Open control menu",
        "menu_title": "🎮 CS2 Control Center",
        "choose_server": "Choose an authorized server",
        "choose_server_hint": "Only servers available in this Guild and channel are shown.",
        "server_title": "🖥️ {server}",
        "server_hint": "Select an action. Permissions are checked again before every step.",
        "choose_action": "Choose an available action",
        "previous": "Previous",
        "next": "Next",
        "back": "Servers",
        "page": "Page {page}/{pages}",
        "no_access": "No authorized CS2 operation is available here.",
        "not_owner": "This menu belongs to the member who mentioned the Bot. Mention the Bot to open your own menu.",
        "expired": "This menu expired. Use @Bot or /cs2 menu to open a new one.",
        "published": "The result or confirmation card was posted in this channel.",
        "search_title": "Search plugin market",
        "search_label": "Search query",
        "search_placeholder": "Plugin name, author, or tag",
        "console_title": "Send a game console command",
        "console_label": "Command",
        "console_placeholder": "status",
        "agent_title": "Ask the server AI Agent",
        "agent_label": "Question or task",
        "agent_placeholder": "Check the server status and recent errors",
        "search_results": "🔎 Plugin results for **{query}**",
        "search_result_hint": "Choose a plugin to view details.",
        "managed_plugins": "🧩 Managed plugins on **{server}**",
        "managed_hint": "Choose a managed plugin to build an immutable upgrade plan.",
        "plugin_detail": "🧩 {plugin}",
        "install": "Plan installation",
        "close": "Close",
        "no_plugins": "No plugins were found.",
        "status_a2s_online": "A2S online",
        "status_a2s_unavailable": "A2S unavailable",
        "status_online_state": "Availability",
        "status_online": "Server Online",
        "status_offline": "Server offline or query failed",
        "status_panel": "Panel status",
        "status_endpoint": "Query address",
        "status_game_address": "Game address",
        "status_server_name": "Server name",
        "status_cs2_version": "Version",
        "status_recorded_version": "Recorded version",
        "status_map": "Map",
        "status_configured_map": "Configured map",
        "status_players": "Players",
        "status_bots": "Bots {count}",
        "status_configured_slots": "Configured slots",
        "status_game": "Game",
        "status_platform": "Platform",
        "status_security": "Security",
        "status_latency": "Ping",
        "status_updated": "Updated",
        "status_disk_directory": "Directory",
        "status_disk_total": "Total",
        "status_disk_usage": "Usage",
        "status_unknown": "unknown",
        "status_vac_on": "VAC on",
        "status_vac_off": "VAC off",
        "status_password_yes": "Password yes",
        "status_password_no": "Password no",
    },
    "zh": {
        "launcher_title": "🎮 CS2 服务器控制中心",
        "launcher_body": "打开仅你可见的控制菜单；服务器和操作会根据你当前的实际权限自动过滤。",
        "open": "打开控制菜单",
        "menu_title": "🎮 CS2 控制中心",
        "choose_server": "选择已授权服务器",
        "choose_server_hint": "只显示当前 Guild 与频道中你有权使用的服务器。",
        "server_title": "🖥️ {server}",
        "server_hint": "请选择操作；系统会在每一步执行前重新检查权限。",
        "choose_action": "选择可用操作",
        "previous": "上一页",
        "next": "下一页",
        "back": "返回服务器",
        "page": "第 {page}/{pages} 页",
        "no_access": "当前频道没有你可使用的 CS2 操作。",
        "not_owner": "此菜单仅限 @Bot 的发起者操作；请自行 @Bot 打开你的菜单。",
        "expired": "此菜单已过期，请重新 @Bot 或使用 /cs2 menu。",
        "published": "结果或确认卡已经发布到当前频道。",
        "search_title": "搜索插件市场",
        "search_label": "搜索内容",
        "search_placeholder": "插件名称、作者或标签",
        "console_title": "发送游戏控制台命令",
        "console_label": "命令",
        "console_placeholder": "status",
        "agent_title": "询问服务器 AI Agent",
        "agent_label": "问题或任务",
        "agent_placeholder": "检查服务器状态和最近错误",
        "search_results": "🔎 **{query}** 的插件搜索结果",
        "search_result_hint": "选择插件查看详情。",
        "managed_plugins": "🧩 **{server}** 的已托管插件",
        "managed_hint": "选择插件以生成不可变升级计划。",
        "plugin_detail": "🧩 {plugin}",
        "install": "生成安装计划",
        "close": "关闭",
        "no_plugins": "没有找到插件。",
        "status_a2s_online": "A2S 在线",
        "status_a2s_unavailable": "A2S 不可用",
        "status_online_state": "在线状态",
        "status_online": "服务器在线",
        "status_offline": "服务器离线或查询失败",
        "status_panel": "面板状态",
        "status_endpoint": "查询地址",
        "status_game_address": "游戏地址",
        "status_server_name": "服务器名称",
        "status_cs2_version": "版本",
        "status_recorded_version": "已记录版本",
        "status_map": "地图",
        "status_configured_map": "配置地图",
        "status_players": "玩家",
        "status_bots": "Bot {count}",
        "status_configured_slots": "配置人数",
        "status_game": "游戏",
        "status_platform": "平台",
        "status_security": "安全",
        "status_latency": "延迟",
        "status_updated": "更新时间",
        "status_disk_directory": "目录占用",
        "status_disk_total": "磁盘总量",
        "status_disk_usage": "占用率",
        "status_unknown": "未取得",
        "status_vac_on": "VAC 开启",
        "status_vac_off": "VAC 关闭",
        "status_password_yes": "有密码",
        "status_password_no": "无密码",
    },
}

_ACTION_METADATA: tuple[tuple[str, DiscordCapability, str, str, str], ...] = (
    ("status", DiscordCapability.STATUS, "📊", "Status", "状态"),
    ("start", DiscordCapability.START, "▶️", "Start server", "启动服务器"),
    ("stop", DiscordCapability.STOP, "⏹️", "Stop server", "停止服务器"),
    ("restart", DiscordCapability.RESTART, "🔄", "Restart server", "重启服务器"),
    ("update", DiscordCapability.UPDATE, "⬆️", "Update CS2", "更新 CS2"),
    ("validate", DiscordCapability.VALIDATE, "🧰", "Validate files", "验证文件"),
    (
        "plugin_search",
        DiscordCapability.PLUGIN_BROWSE,
        "🔎",
        "Search plugin market",
        "搜索插件市场",
    ),
    (
        "plugin_list",
        DiscordCapability.PLUGIN_BROWSE,
        "📦",
        "List managed plugins",
        "查看已托管插件",
    ),
    (
        "plugin_install",
        DiscordCapability.PLUGIN_INSTALL,
        "➕",
        "Install market plugin",
        "安装市场插件",
    ),
    (
        "plugin_upgrade",
        DiscordCapability.PLUGIN_UPGRADE,
        "🧩",
        "Upgrade managed plugin",
        "升级已托管插件",
    ),
    (
        "game_console",
        DiscordCapability.GAME_CONSOLE,
        "⌨️",
        "Game console command",
        "游戏控制台命令",
    ),
    ("agent_ask", DiscordCapability.AGENT_ASK, "✨", "Ask AI Agent", "询问 AI Agent"),
    (
        "agent_reset",
        DiscordCapability.AGENT_ASK,
        "🧹",
        "Reset AI context",
        "重置 AI 上下文",
    ),
)


def locale_key(value: object) -> str:
    return "zh" if str(value or "").casefold().startswith("zh") else "en"


def text(locale: object, key: str, **values: object) -> str:
    language = locale_key(locale)
    return _TEXT[language][key].format(**values)


def normalize_message_trigger(content: str, bot_user_id: int | str) -> str:
    value = unicodedata.normalize("NFKC", content or "")
    value = re.sub(rf"<@!?{re.escape(str(bot_user_id))}>", " ", value)
    return " ".join(value.split()).casefold().strip(_TRAILING_PUNCTUATION)


def is_exact_wake_word(content: str, bot_user_id: int | str) -> bool:
    return normalize_message_trigger(content, bot_user_id) in _WAKE_WORDS


def is_leading_bot_mention(content: str, bot_user_id: int | str) -> bool:
    value = unicodedata.normalize("NFKC", content or "").lstrip()
    return re.match(rf"<@!?{re.escape(str(bot_user_id))}>", value) is not None


def menu_issued_at() -> int:
    return int(time.time())


def menu_is_expired(issued_at: int, *, now: int | None = None) -> bool:
    current = int(time.time()) if now is None else now
    return issued_at <= 0 or current - issued_at > MENU_LIFETIME_SECONDS or issued_at > current + 30


def launcher_is_expired(issued_at: int, *, now: int | None = None) -> bool:
    current = int(time.time()) if now is None else now
    return (
        issued_at <= 0
        or current - issued_at > LAUNCHER_LIFETIME_SECONDS
        or issued_at > current + 30
    )


def _layout(
    title: str,
    body: str,
    *controls: discord.ui.Item,
    timeout: float,
    accent: discord.Color | int = discord.Color.blurple(),
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    container = discord.ui.Container(accent_color=accent)
    container.add_item(discord.ui.TextDisplay(f"## {title}"))
    container.add_item(discord.ui.TextDisplay(body))
    if controls:
        container.add_item(discord.ui.Separator())
        for control in controls:
            container.add_item(control)
    view.add_item(container)
    return view


def launcher_view(locale: object, *, issued_at: int | None = None) -> discord.ui.LayoutView:
    issued_at = issued_at or menu_issued_at()
    button = discord.ui.Button(
        label=text(locale, "open"),
        emoji="🎛️",
        style=discord.ButtonStyle.primary,
        custom_id=f"cs2:menu:open:{issued_at}",
    )
    return _layout(
        text(locale, "launcher_title"),
        text(locale, "launcher_body"),
        discord.ui.ActionRow(button),
        timeout=LAUNCHER_LIFETIME_SECONDS,
    )


def no_access_view(locale: object) -> discord.ui.LayoutView:
    return _layout(
        text(locale, "menu_title"),
        text(locale, "no_access"),
        timeout=MENU_LIFETIME_SECONDS,
        accent=discord.Color.greyple(),
    )


def server_picker_view(
    locale: object,
    servers: list[dict],
    *,
    issued_at: int,
    requester_user_id: int | str,
    page: int,
) -> discord.ui.LayoutView:
    pages = max(1, (len(servers) + SERVER_PAGE_SIZE - 1) // SERVER_PAGE_SIZE)
    page = min(max(page, 0), pages - 1)
    visible = servers[page * SERVER_PAGE_SIZE : (page + 1) * SERVER_PAGE_SIZE]
    options = [
        discord.SelectOption(
            label=str(item["name"])[:100],
            value=str(item["id"]),
            description=f"ID {item['id']} · {item['capability_count']} actions"[:100],
            emoji="🖥️",
        )
        for item in visible
    ]
    selector = discord.ui.Select(
        placeholder=text(locale, "choose_server"),
        options=options,
        custom_id=f"cs2:menu:server:{issued_at}:{requester_user_id}:{page}",
    )
    buttons = discord.ui.ActionRow(
        discord.ui.Button(
            label=text(locale, "previous"),
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"cs2:menu:page:{issued_at}:{requester_user_id}:{page - 1}",
            disabled=page == 0,
        ),
        discord.ui.Button(
            label=text(locale, "next"),
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"cs2:menu:page:{issued_at}:{requester_user_id}:{page + 1}",
            disabled=page >= pages - 1,
        ),
    )
    body = f"{text(locale, 'choose_server_hint')}\n\n{text(locale, 'page', page=page + 1, pages=pages)}"
    return _layout(
        text(locale, "menu_title"),
        body,
        discord.ui.ActionRow(selector),
        buttons,
        timeout=MENU_LIFETIME_SECONDS,
    )


def action_capability(action: str) -> DiscordCapability | None:
    for item_action, capability, _emoji, _en, _zh in _ACTION_METADATA:
        if item_action == action:
            return capability
    return None


def control_view(
    locale: object,
    *,
    server_id: int,
    server_name: str,
    capabilities: Iterable[str],
    issued_at: int,
    requester_user_id: int | str,
) -> discord.ui.LayoutView:
    allowed = set(capabilities)
    language = locale_key(locale)
    options = [
        discord.SelectOption(
            label=(zh_label if language == "zh" else en_label)[:100],
            value=action,
            emoji=emoji,
        )
        for action, capability, emoji, en_label, zh_label in _ACTION_METADATA
        if capability.value in allowed
    ]
    controls: list[discord.ui.Item] = []
    if options:
        controls.append(
            discord.ui.ActionRow(
                discord.ui.Select(
                    placeholder=text(locale, "choose_action"),
                    options=options,
                    custom_id=(f"cs2:menu:action:{issued_at}:{requester_user_id}:{server_id}"),
                )
            )
        )
    controls.append(
        discord.ui.ActionRow(
            discord.ui.Button(
                label=text(locale, "back"),
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"cs2:menu:page:{issued_at}:{requester_user_id}:0",
            )
        )
    )
    body = text(locale, "server_hint") if options else text(locale, "no_access")
    return _layout(
        text(locale, "server_title", server=server_name),
        body,
        *controls,
        timeout=MENU_LIFETIME_SECONDS,
        accent=discord.Color.green() if options else discord.Color.greyple(),
    )


def plugin_picker_view(
    locale: object,
    *,
    title: str,
    hint: str,
    options: list[discord.SelectOption],
    custom_id: str,
    issued_at: int,
    requester_user_id: int | str,
    server_id: int,
    page: int,
    pages: int,
    page_kind: str,
) -> discord.ui.LayoutView:
    if not options:
        return _layout(
            title,
            text(locale, "no_plugins"),
            discord.ui.ActionRow(
                discord.ui.Button(
                    label=text(locale, "back"),
                    emoji="↩️",
                    style=discord.ButtonStyle.secondary,
                    custom_id=(f"cs2:menu:control:{issued_at}:{requester_user_id}:{server_id}"),
                )
            ),
            timeout=MENU_LIFETIME_SECONDS,
            accent=discord.Color.greyple(),
        )
    selector = discord.ui.Select(
        placeholder=hint[:150],
        options=options,
        custom_id=custom_id,
    )
    rows: list[discord.ui.Item] = [discord.ui.ActionRow(selector)]
    page_buttons = discord.ui.ActionRow(
        discord.ui.Button(
            label=text(locale, "previous"),
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                f"cs2:menu:{page_kind}:{issued_at}:{requester_user_id}:{server_id}:{page - 1}"
            ),
            disabled=page == 0,
        ),
        discord.ui.Button(
            label=text(locale, "next"),
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                f"cs2:menu:{page_kind}:{issued_at}:{requester_user_id}:{server_id}:{page + 1}"
            ),
            disabled=page >= pages - 1,
        ),
        discord.ui.Button(
            label=text(locale, "back"),
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"cs2:menu:control:{issued_at}:{requester_user_id}:{server_id}",
        ),
    )
    rows.append(page_buttons)
    return _layout(
        title,
        f"{hint}\n\n{text(locale, 'page', page=page + 1, pages=pages)}",
        *rows,
        timeout=MENU_LIFETIME_SECONDS,
    )


class MenuInputModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        locale: object,
        title_key: str,
        label_key: str,
        placeholder_key: str,
        custom_id: str,
        callback: Callable[[discord.Interaction, str], Awaitable[None]],
        style: discord.TextStyle = discord.TextStyle.short,
        max_length: int = 1000,
    ) -> None:
        super().__init__(
            title=text(locale, title_key)[:45],
            custom_id=custom_id,
            timeout=MENU_LIFETIME_SECONDS,
        )
        self._menu_callback = callback
        self.value_input = discord.ui.TextInput(
            label=text(locale, label_key)[:45],
            placeholder=text(locale, placeholder_key)[:100],
            style=style,
            min_length=1,
            max_length=max_length,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._menu_callback(interaction, str(self.value_input.value))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        message = "Unable to complete this menu action. Reopen the menu and try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
