"""隔离覆盖系统清理的策略、扫描和执行分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import system_cleanup_service as cleanup


def _server(**overrides):
    values = {
        "game_directory": "/srv/cs2",
        "cleanup_retain_days": 7,
        "cleanup_targets": ["game_logs"],
        "cleanup_auto_enabled": False,
        "sudo_password": None,
        "ssh_password": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_system_cleanup_helpers_cover_all_allowlisted_targets():
    assert cleanup.clamp_retain_days(None) == 7
    assert cleanup.clamp_retain_days("bad") == 7
    assert cleanup.clamp_retain_days(0) == 1
    assert cleanup.clamp_retain_days(1000) == 90
    assert cleanup.normalize_schedule_value(None) == "03:30"
    assert cleanup.normalize_schedule_value("3:05") == "03:05"
    for value in ("x", "24:00", "02:60"):
        with pytest.raises(ValueError):
            cleanup.normalize_schedule_value(value)
    assert cleanup.normalize_targets(None) == ["game_logs"]
    assert cleanup.normalize_targets(["tmp", "tmp", "journal"]) == ["tmp", "journal"]
    with pytest.raises(ValueError):
        cleanup.normalize_targets(["unknown"])
    for target in cleanup.SYSTEM_TARGET_IDS[1:]:
        command = cleanup.target_command(target, 10)
        assert command and "rm -rf /" not in command
    with pytest.raises(ValueError):
        cleanup.target_command("bad", 7)
    assert cleanup.can_apply_target("game_logs", "none")
    assert cleanup.can_apply_target("journal", "root")
    assert not cleanup.can_apply_target("journal", "none")
    assert cleanup.parse_size("") == 0
    assert cleanup.parse_size("512K") == 512 * 1024
    assert cleanup.parse_size("bad 123") == 123
    assert cleanup.parse_size("nothing") == 0
    assert cleanup.parse_size("1.5T") == int(1.5 * 1024**4)
    assert cleanup.manual_execute_commands(["game_logs", "thumbnails"], 7)[0].startswith("#")
    assert cleanup.manual_setup_commands([], 7, "03:30")[-1].endswith("true")


@pytest.mark.asyncio
async def test_system_cleanup_policy_scan_and_failed_connection(monkeypatch):
    service = cleanup.SystemCleanupService()
    task = SimpleNamespace(
        schedule_value="bad", last_run="last", next_run="next", last_status="ok", last_error=None, run_count=2
    )
    policy = service.policy_from_server(
        _server(cleanup_targets=["not-a-target"], cleanup_retain_days=999, sudo_password="secret"), task
    )
    assert policy["targets"] == ["game_logs"]
    assert policy["retain_days"] == 90
    assert policy["has_sudo_password"] is True
    assert policy["schedule_value"] == "03:30"

    monkeypatch.setattr(cleanup.game_cleanup_service, "_ensure_connected", AsyncMock(return_value=(False, "down")))
    with pytest.raises(RuntimeError, match="Connection failed"):
        await service.scan(AsyncMock(), _server())

    ssh = SimpleNamespace(
        execute_command=AsyncMock(side_effect=lambda command, timeout=20: (True, "1024", "")),
        execute_sudo_command=AsyncMock(return_value=(True, "", "")),
    )
    monkeypatch.setattr(cleanup.game_cleanup_service, "_ensure_connected", AsyncMock(return_value=(True, "")))

    class _Runner:
        async def resolve_privilege(self):
            return "none"

    monkeypatch.setattr(cleanup, "SshManagerHostRunner", lambda *_args: _Runner())
    events = [event async for event in service.iter_scan(ssh, _server(), retain_days=3)]
    assert events[0]["phase"] == "privilege"
    assert len([event for event in events if event["type"] == "target"]) == len(cleanup.SYSTEM_TARGET_IDS)
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["privilege"] == "none"
    assert events[-1]["data"]["manual_execute"]


@pytest.mark.asyncio
async def test_system_cleanup_apply_root_sudo_failures_and_game_logs(monkeypatch):
    service = cleanup.SystemCleanupService()
    server = _server(cleanup_targets=list(cleanup.SYSTEM_TARGET_IDS))
    monkeypatch.setattr(cleanup.game_cleanup_service, "_ensure_connected", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(
        cleanup.game_cleanup_service,
        "purge_old_logs",
        AsyncMock(return_value=(True, {"deleted_count": 2, "freed_bytes_estimate": 20}, "")),
    )

    class _Runner:
        def __init__(self, privilege):
            self.privilege = privilege
            self.commands = []

        async def resolve_privilege(self):
            return self.privilege

        async def run(self, command, *, timeout=180):
            self.commands.append(command)
            if command.startswith("find /var/log"):
                return 1, "", "permission denied"
            return 0, "", ""

        async def run_privileged(self, command, *, timeout=180):
            self.commands.append("privileged:" + command)
            if "journalctl" in command or command.startswith("find /var/log"):
                return 1, "", "journal failed"
            return 0, "", ""

    runner = _Runner("sudo")
    monkeypatch.setattr(cleanup, "SshManagerHostRunner", lambda *_args: runner)
    result = await service.apply(AsyncMock(), server, cleanup.SYSTEM_TARGET_IDS, retain_days=7)
    assert result["deleted_count"] == 2
    assert "game_logs" in result["applied"]
    assert "journal" in [item["id"] for item in result["failed"]]
    assert "rotated_logs" in [item["id"] for item in result["failed"]]
    assert result["success"] is False

    none_runner = _Runner("none")
    monkeypatch.setattr(cleanup, "SshManagerHostRunner", lambda *_args: none_runner)
    result = await service.apply(AsyncMock(), server, ["game_logs", "journal"], retain_days=7)
    assert result["success"] is False
    assert result["skipped"][0]["id"] == "journal"
    assert result["manual_execute"]


@pytest.mark.asyncio
async def test_system_cleanup_scheduled_adds_manual_commands(monkeypatch):
    service = cleanup.SystemCleanupService()
    server = _server(cleanup_targets=["journal"])
    monkeypatch.setattr(cleanup.game_cleanup_service, "_ensure_connected", AsyncMock(return_value=(True, "")))

    class _Runner:
        async def resolve_privilege(self):
            return "none"

    monkeypatch.setattr(cleanup, "SshManagerHostRunner", lambda *_args: _Runner())
    ok, message = await service.run_scheduled(AsyncMock(), server)
    assert not ok
    assert "sudo" in message
