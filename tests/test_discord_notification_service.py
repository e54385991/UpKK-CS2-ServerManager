#!/usr/bin/env python3
"""Tests for Discord notification payloads and settings safety."""

import asyncio

from modules.models import AuthType, Server
from services.discord_notification_service import (
    ERROR_COLOR,
    EVENT_AUTO_UPDATE,
    EVENT_CRASH_RESTART,
    EVENT_MANUAL_UPDATE,
    EVENT_PLUGIN_UPDATE,
    EVENT_S3_BACKUP,
    MAX_FIELD_NAME_LENGTH,
    MAX_FIELD_VALUE_LENGTH,
    SUCCESS_COLOR,
    TEST_COLOR,
    IN_PROGRESS_COLOR,
    DiscordNotificationService,
)


VALID_WEBHOOK = "https://discord.com/api/webhooks/1234567890/test-token"


def make_server(**overrides):
    data = {
        "id": 7,
        "user_id": 1,
        "name": "Dust Hub",
        "host": "192.0.2.10",
        "ssh_user": "steam",
        "auth_type": AuthType.PASSWORD,
        "game_port": 27015,
    }
    data.update(overrides)
    return Server(**data)


def test_validate_webhook_url_accepts_discord_webhooks_only():
    service = DiscordNotificationService()

    valid, error = service.validate_webhook_url(VALID_WEBHOOK)
    assert valid is True
    assert error == ""

    invalid_urls = [
        "http://discord.com/api/webhooks/123/token",
        "https://example.com/api/webhooks/123/token",
        "https://discord.com/api/not-webhooks/123/token",
        "https://discord.com/api/webhooks/not-a-number/token",
        "https://discord.com/api/webhooks/123/",
    ]

    for url in invalid_urls:
        valid, error = service.validate_webhook_url(url)
        assert valid is False
        assert error


def test_build_payload_uses_success_and_failure_embed_templates():
    service = DiscordNotificationService()
    server = make_server()

    success_payload = service.build_payload(
        server,
        EVENT_MANUAL_UPDATE,
        "update",
        True,
        "Update completed",
    )
    failure_payload = service.build_payload(
        server,
        EVENT_MANUAL_UPDATE,
        "update",
        False,
        "Update failed",
    )

    assert success_payload["allowed_mentions"] == {"parse": []}
    assert success_payload["embeds"][0]["color"] == SUCCESS_COLOR
    assert failure_payload["embeds"][0]["color"] == ERROR_COLOR
    assert success_payload["embeds"][0]["footer"]["text"] == "CS2 Server Manager - Discord notifications"
    assert any(
        field["name"] == "Result" and field["value"] == "Failed"
        for field in failure_payload["embeds"][0]["fields"]
    )


def test_build_payload_supports_in_progress_state():
    service = DiscordNotificationService()
    payload = service.build_payload(
        make_server(), EVENT_AUTO_UPDATE, "auto_update", True,
        "Starting update", title="Automatic update started", state="in_progress",
    )
    embed = payload["embeds"][0]
    assert embed["color"] == IN_PROGRESS_COLOR
    assert "is starting" in embed["description"]
    assert any(field["name"] == "Result" and field["value"] == "In Progress" for field in embed["fields"])


def test_build_payload_truncates_discord_field_limits():
    service = DiscordNotificationService()
    server = make_server()
    long_name = "N" * (MAX_FIELD_NAME_LENGTH + 30)
    long_value = "V" * (MAX_FIELD_VALUE_LENGTH + 30)

    payload = service.build_payload(
        server,
        EVENT_PLUGIN_UPDATE,
        "install_github_plugin",
        True,
        long_value,
        title="T" * 300,
        details={long_name: long_value},
    )

    embed = payload["embeds"][0]
    assert len(embed["title"]) == 256

    detail_field = next(field for field in embed["fields"] if field["name"].startswith("N"))
    message_field = next(field for field in embed["fields"] if field["name"] == "Message")

    assert len(detail_field["name"]) == MAX_FIELD_NAME_LENGTH
    assert len(detail_field["value"]) == MAX_FIELD_VALUE_LENGTH
    assert len(message_field["value"]) == MAX_FIELD_VALUE_LENGTH
    assert detail_field["name"].endswith("...")
    assert detail_field["value"].endswith("...")
    assert message_field["value"].endswith("...")


def test_notify_respects_event_switches_and_does_not_send_when_disabled():
    service = DiscordNotificationService()
    calls = []

    async def fake_post(webhook_url, payload):
        calls.append((webhook_url, payload))
        return True, None

    service._post_payload = fake_post

    disabled_server = make_server(
        discord_webhook_url=VALID_WEBHOOK,
        discord_notifications_enabled=False,
    )
    delivered = asyncio.run(
        service.notify(disabled_server, EVENT_AUTO_UPDATE, "update", True, "Done")
    )
    assert delivered is False
    assert calls == []

    manual_disabled_server = make_server(
        discord_webhook_url=VALID_WEBHOOK,
        discord_notifications_enabled=True,
        discord_notify_manual_updates=False,
    )
    delivered = asyncio.run(
        service.notify(manual_disabled_server, EVENT_MANUAL_UPDATE, "validate", True, "Done")
    )
    assert delivered is False
    assert calls == []

    plugin_enabled_server = make_server(
        discord_webhook_url=VALID_WEBHOOK,
        discord_notifications_enabled=True,
        discord_notify_plugin_updates=True,
    )
    delivered = asyncio.run(
        service.notify(plugin_enabled_server, EVENT_PLUGIN_UPDATE, "backup_plugins", True, "Done")
    )
    assert delivered is True
    assert calls[0][0] == VALID_WEBHOOK
    assert calls[0][1]["allowed_mentions"] == {"parse": []}


def test_queue_notify_returns_before_background_delivery_and_respects_s3_switch():
    service = DiscordNotificationService()
    calls = []

    async def fake_post(webhook_url, payload):
        calls.append((webhook_url, payload))
        return True, None

    async def run_test():
        service._post_payload = fake_post
        disabled_server = make_server(
            discord_webhook_url=VALID_WEBHOOK,
            discord_notifications_enabled=True,
            discord_notify_s3_backups=False,
        )
        assert service.queue_notify(
            disabled_server,
            EVENT_S3_BACKUP,
            "s3_backup_upload",
            True,
            "S3 upload completed",
        ) is False
        assert calls == []

        enabled_server = make_server(
            discord_webhook_url=VALID_WEBHOOK,
            discord_notifications_enabled=True,
            discord_notify_s3_backups=True,
        )
        queued = service.queue_notify(
            enabled_server,
            EVENT_S3_BACKUP,
            "s3_backup_upload",
            True,
            "S3 upload completed",
        )
        assert queued is True
        assert calls == []
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls[0][0] == VALID_WEBHOOK
        assert calls[0][1]["embeds"][0]["title"] == "S3 backup upload: Success"

    asyncio.run(run_test())


def test_queue_notify_rate_limits_crash_restart_notifications():
    service = DiscordNotificationService()
    calls = []

    async def fake_post(webhook_url, payload):
        calls.append((webhook_url, payload))
        return True, None

    async def run_test():
        service._post_payload = fake_post
        server = make_server(
            discord_webhook_url=VALID_WEBHOOK,
            discord_notifications_enabled=True,
            discord_notify_crash_restarts=True,
            discord_crash_restart_min_interval_minutes=10,
        )

        first = service.queue_notify(
            server,
            EVENT_CRASH_RESTART,
            "auto_restart",
            True,
            "Auto-restart completed",
            rate_limit_minutes=server.discord_crash_restart_min_interval_minutes,
            rate_limit_scope="auto_restart",
        )
        second = service.queue_notify(
            server,
            EVENT_CRASH_RESTART,
            "auto_restart",
            False,
            "Auto-restart failed",
            rate_limit_minutes=server.discord_crash_restart_min_interval_minutes,
            rate_limit_scope="auto_restart",
        )

        assert first is True
        assert second is False
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert calls[0][1]["embeds"][0]["title"] == "Crash / auto-restart: Success"

    asyncio.run(run_test())


def test_send_test_uses_test_color_and_safe_mentions():
    service = DiscordNotificationService()
    calls = []

    async def fake_post(webhook_url, payload):
        calls.append((webhook_url, payload))
        return True, None

    service._post_payload = fake_post
    server = make_server(
        discord_webhook_url=VALID_WEBHOOK,
        discord_channel_name="#updates",
    )

    success, message = asyncio.run(service.send_test(server, "Hello Discord"))

    assert success is True
    assert message == "Discord test notification sent successfully"
    assert calls[0][1]["allowed_mentions"] == {"parse": []}
    assert calls[0][1]["embeds"][0]["color"] == TEST_COLOR


def test_settings_response_does_not_expose_webhook_url():
    from api.routes.servers import build_discord_settings_response

    server = make_server(
        discord_webhook_url=VALID_WEBHOOK,
        discord_notifications_enabled=True,
        discord_channel_name="#updates",
    )
    server.discord_crash_restart_min_interval_minutes = None

    response = build_discord_settings_response(server)
    data = response.model_dump()

    assert data["webhook_configured"] is True
    assert data["discord_channel_name"] == "#updates"
    assert data["discord_notify_crash_restarts"] is True
    assert data["discord_crash_restart_min_interval_minutes"] == 10
    assert "discord_webhook_url" not in data
