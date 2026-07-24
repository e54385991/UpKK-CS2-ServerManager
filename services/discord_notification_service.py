"""
Discord webhook notifications for server update events.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx

from modules.http_helper import HTTPHelper, http_helper
from modules.models import Server
from modules.utils import get_current_time

logger = logging.getLogger(__name__)


DISCORD_HOSTS = {
    "discord.com",
    "www.discord.com",
    "discordapp.com",
    "www.discordapp.com",
    "canary.discord.com",
    "ptb.discord.com",
}

EVENT_AUTO_UPDATE = "auto_update"
EVENT_MANUAL_UPDATE = "manual_update"
EVENT_PLUGIN_UPDATE = "plugin_update"
EVENT_S3_BACKUP = "s3_backup"
EVENT_CRASH_RESTART = "crash_restart"

SUCCESS_COLOR = 0x2ECC71
ERROR_COLOR = 0xE74C3C
TEST_COLOR = 0x5865F2
IN_PROGRESS_COLOR = 0x3498DB

MAX_TITLE_LENGTH = 256
MAX_DESCRIPTION_LENGTH = 4096
MAX_FIELD_NAME_LENGTH = 256
MAX_FIELD_VALUE_LENGTH = 1024


class DiscordNotificationService:
    """Build and send Discord webhook notifications."""

    def __init__(self, http_client: Optional[HTTPHelper] = None) -> None:
        self._http = http_client or http_helper
        self._background_tasks: Set[asyncio.Task] = set()
        self._rate_limit_last_sent: Dict[Tuple[int, str, str], Any] = {}

    def validate_webhook_url(self, webhook_url: Optional[str]) -> Tuple[bool, str]:
        """Validate that a URL is a Discord webhook URL."""
        if not webhook_url:
            return False, "Discord webhook URL is required"

        parsed = urlparse(webhook_url.strip())
        if parsed.scheme != "https":
            return False, "Discord webhook URL must use HTTPS"

        hostname = (parsed.hostname or "").lower()
        if hostname not in DISCORD_HOSTS:
            return False, "Discord webhook URL must be hosted by discord.com or discordapp.com"

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 4 or parts[0] != "api" or parts[1] != "webhooks":
            return False, "Discord webhook URL must be an /api/webhooks/{id}/{token} URL"

        if not parts[2].isdigit() or not parts[3]:
            return False, "Discord webhook URL is missing the webhook id or token"

        return True, ""

    def webhook_configured(self, server: Server) -> bool:
        return bool(server.discord_webhook_url and server.discord_webhook_url.strip())

    def should_notify(self, server: Server, event_type: str) -> bool:
        if not server.discord_notifications_enabled or not self.webhook_configured(server):
            return False

        if event_type == EVENT_AUTO_UPDATE:
            return bool(server.discord_notify_auto_updates)
        if event_type == EVENT_MANUAL_UPDATE:
            return bool(server.discord_notify_manual_updates)
        if event_type == EVENT_PLUGIN_UPDATE:
            return bool(server.discord_notify_plugin_updates)
        if event_type == EVENT_S3_BACKUP:
            return bool(server.discord_notify_s3_backups)
        if event_type == EVENT_CRASH_RESTART:
            return bool(server.discord_notify_crash_restarts)
        return False

    def queue_notify(
        self,
        server: Server,
        event_type: str,
        action: str,
        success: bool,
        message: str,
        *,
        title: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        rate_limit_minutes: Optional[int] = None,
        rate_limit_scope: Optional[str] = None,
        state: Optional[str] = None,
    ) -> bool:
        """Queue a Discord notification without waiting for network I/O."""
        if not self.should_notify(server, event_type):
            return False

        if self._is_rate_limited(server, event_type, action, rate_limit_minutes, rate_limit_scope):
            return False

        webhook_url = server.discord_webhook_url or ""
        valid, error = self.validate_webhook_url(webhook_url)
        if not valid:
            logger.warning(
                "Discord notification skipped for server %s: %s",
                server.id,
                error,
            )
            return False

        payload = self.build_payload(
            server,
            event_type,
            action,
            success,
            message,
            title=title,
            details=details,
            state=state,
        )

        try:
            task = asyncio.create_task(self._send_payload(webhook_url, payload, server.id, action))
        except RuntimeError:
            logger.warning(
                "Discord notification could not be queued for server %s, action %s: no running event loop",
                server.id,
                action,
            )
            return False

        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return True

    async def shutdown(self) -> None:
        """Cancel and await queued webhook deliveries during application shutdown."""
        tasks = list(self._background_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    def _is_rate_limited(
        self,
        server: Server,
        event_type: str,
        action: str,
        rate_limit_minutes: Optional[int],
        rate_limit_scope: Optional[str],
    ) -> bool:
        if not rate_limit_minutes or rate_limit_minutes <= 0:
            return False

        server_id = int(server.id or 0)
        key = (server_id, event_type, rate_limit_scope or action)
        now = get_current_time()
        last_sent = self._rate_limit_last_sent.get(key)
        if last_sent:
            elapsed_seconds = (now - last_sent).total_seconds()
            if elapsed_seconds < rate_limit_minutes * 60:
                remaining_seconds = int(rate_limit_minutes * 60 - elapsed_seconds)
                logger.info(
                    "Discord notification suppressed by rate limit for server %s, event %s, action %s. Remaining: %ss",
                    server.id,
                    event_type,
                    action,
                    remaining_seconds,
                )
                return True

        self._rate_limit_last_sent[key] = now
        return False

    async def notify(
        self,
        server: Server,
        event_type: str,
        action: str,
        success: bool,
        message: str,
        *,
        title: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        state: Optional[str] = None,
    ) -> bool:
        """Send a Discord notification if enabled for the server and event."""
        if not self.should_notify(server, event_type):
            return False

        webhook_url = server.discord_webhook_url or ""
        valid, error = self.validate_webhook_url(webhook_url)
        if not valid:
            logger.warning(
                "Discord notification skipped for server %s: %s",
                server.id,
                error,
            )
            return False

        payload = self.build_payload(
            server,
            event_type,
            action,
            success,
            message,
            title=title,
            details=details,
            state=state,
        )
        return await self._send_payload(webhook_url, payload, server.id, action)

    async def send_test(self, server: Server, message: Optional[str] = None) -> Tuple[bool, str]:
        """Send a test notification to the saved Discord webhook."""
        webhook_url = server.discord_webhook_url or ""
        valid, error = self.validate_webhook_url(webhook_url)
        if not valid:
            return False, error

        payload = self.build_payload(
            server,
            EVENT_MANUAL_UPDATE,
            "discord_test",
            True,
            message or "Discord notifications are configured correctly.",
            title="Discord notification test",
            details={"Channel": server.discord_channel_name or "Webhook default channel"},
            color=TEST_COLOR,
        )
        delivered, send_error = await self._post_payload(webhook_url, payload)
        if not delivered:
            return False, send_error or "Failed to send Discord notification"
        return True, "Discord test notification sent successfully"

    def build_payload(
        self,
        server: Server,
        event_type: str,
        action: str,
        success: bool,
        message: str,
        *,
        title: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        color: Optional[int] = None,
        state: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_state = state or ("success" if success else "failed")
        status_text = {
            "in_progress": "In Progress",
            "success": "Success",
            "failed": "Failed",
        }.get(normalized_state, "Success" if success else "Failed")
        event_label = self._event_label(event_type)
        embed_title = title or f"{event_label}: {status_text}"
        default_color = (
            IN_PROGRESS_COLOR
            if normalized_state == "in_progress"
            else (SUCCESS_COLOR if success else ERROR_COLOR)
        )
        embed_color = color if color is not None else default_color

        fields = [
            {"name": "Server", "value": self._server_label(server), "inline": True},
            {"name": "Action", "value": action, "inline": True},
            {"name": "Result", "value": status_text, "inline": True},
        ]

        if details:
            for name, value in details.items():
                if value is None or value == "":
                    continue
                fields.append(
                    {
                        "name": self._truncate(str(name), MAX_FIELD_NAME_LENGTH),
                        "value": self._truncate(str(value), MAX_FIELD_VALUE_LENGTH),
                        "inline": False,
                    }
                )

        fields.append(
            {
                "name": "Message",
                "value": self._truncate(message or "No details provided.", MAX_FIELD_VALUE_LENGTH),
                "inline": False,
            }
        )

        descriptions = {
            "in_progress": f"{server.name} is starting an update-related operation.",
            "success": f"{server.name} has completed an update-related operation successfully.",
            "failed": f"{server.name} could not complete an update-related operation.",
        }
        embed: Dict[str, Any] = {
            "title": self._truncate(embed_title, MAX_TITLE_LENGTH),
            "description": self._truncate(
                descriptions.get(
                    normalized_state, f"{server.name} has completed an update-related operation."
                ),
                MAX_DESCRIPTION_LENGTH,
            ),
            "color": embed_color,
            "fields": fields,
            "timestamp": get_current_time().isoformat(),
            "footer": {"text": "CS2 Server Manager - Discord notifications"},
        }

        return {
            "username": "CS2 Server Manager",
            "allowed_mentions": {"parse": []},
            "embeds": [embed],
        }

    async def _post_payload(
        self, webhook_url: str, payload: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """Post a payload without logging the sensitive webhook URL."""
        try:
            response = await self._http.request(
                "POST",
                webhook_url,
                json=payload,
                timeout=10.0,
                follow_redirects=False,
            )

            if 200 <= response.status_code < 300:
                return True, None

            body = response.text.strip()
            if body:
                body = self._truncate(body, 300)
                return False, f"Discord returned HTTP {response.status_code}: {body}"
            return False, f"Discord returned HTTP {response.status_code}"
        except httpx.TimeoutException:
            return False, "Discord request timed out"
        except httpx.RequestError as exc:
            return False, f"Discord request failed: {exc.__class__.__name__}"
        except Exception as exc:
            return False, f"Unexpected Discord error: {exc.__class__.__name__}"

    async def _send_payload(
        self,
        webhook_url: str,
        payload: Dict[str, Any],
        server_id: Optional[int],
        action: str,
    ) -> bool:
        delivered, send_error = await self._post_payload(webhook_url, payload)
        if not delivered:
            logger.warning(
                "Discord notification failed for server %s, action %s: %s",
                server_id,
                action,
                send_error or "unknown error",
            )
        return delivered

    def _event_label(self, event_type: str) -> str:
        return {
            EVENT_AUTO_UPDATE: "Automatic update",
            EVENT_MANUAL_UPDATE: "Server update",
            EVENT_PLUGIN_UPDATE: "Plugin update",
            EVENT_S3_BACKUP: "S3 backup upload",
            EVENT_CRASH_RESTART: "Crash / auto-restart",
        }.get(event_type, "Server notification")

    def _server_label(self, server: Server) -> str:
        host = f"{server.host}:{server.game_port}" if server.host else "Unknown host"
        return self._truncate(f"{server.name} ({host})", MAX_FIELD_VALUE_LENGTH)

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        if limit <= 3:
            return value[:limit]
        return f"{value[: limit - 3]}..."


discord_notification_service = DiscordNotificationService()
