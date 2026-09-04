"""Cover legacy server diagnostics and maintenance endpoints with fakes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.servers import maintenance


class _Db:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None

    async def get(self, _model, _id):
        return None


class _SSH:
    def __init__(self, connect=(True, "ok"), commands=None):
        self.connect_result = connect
        self.commands = list(commands or [])
        self.disconnected = False
        self.last_plugin_backup = {"key": "backup"}

    async def connect(self, _server):
        return self.connect_result

    async def execute_command(self, _command):
        return self.commands.pop(0) if self.commands else (True, "exists", "")

    async def backup_plugins(self, _server):
        return True, "safe"

    async def upload_file(self, *_args):
        return True, ""

    async def extract_archive(self, *_args, **_kwargs):
        return True, ""

    async def disconnect(self):
        self.disconnected = True


def _server(**overrides):
    values = dict(
        id=3,
        user_id=1,
        name="demo",
        game_directory="/srv/cs2",
        status=maintenance.ServerStatus.STOPPED,
        ssh_health_status="healthy",
        consecutive_ssh_failures=0,
        ssh_health_check_interval_hours=2,
        ssh_health_failure_threshold=3,
        is_ssh_down=False,
        last_ssh_success=None,
        last_ssh_failure=None,
        last_ssh_health_check=None,
        enable_ssh_health_monitoring=True,
        game_port=27015,
        github_proxy=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_maintenance_disk_cleanup_and_s3_routes(monkeypatch):
    db = _Db()
    user = SimpleNamespace(id=1)
    servers = [_server(id=3), _server(id=4)]
    monkeypatch.setattr(maintenance.Server, "get_all_by_user", AsyncMock(return_value=servers))
    monkeypatch.setattr(
        "services.system_info_helper.system_info_helper.get_all_servers_disk_space",
        AsyncMock(return_value={3: {"free_gb": 5}, 4: None}),
    )
    all_space = await maintenance.get_all_servers_disk_space(db=db, current_user=user)
    assert all_space["servers"]["3"]["free_gb"] == 5

    server = _server()
    monkeypatch.setattr(maintenance, "get_server_with_permission", AsyncMock(return_value=server))
    ssh = _SSH()
    monkeypatch.setattr(maintenance, "SSHManager", lambda: ssh)
    monkeypatch.setattr(
        maintenance.game_cleanup_service,
        "scan",
        AsyncMock(return_value=(True, {"items": []}, "")),
    )
    assert (await maintenance.scan_server_cleanup(3, db, user))["items"] == []
    monkeypatch.setattr(
        maintenance.game_cleanup_service,
        "delete",
        AsyncMock(return_value=(True, {"deleted": 1}, "")),
    )
    request = SimpleNamespace(mode="logs", paths=[], confirmation_text="CONFIRM")
    assert (await maintenance.delete_server_cleanup_items(3, request, db, user))["deleted"] == 1
    monkeypatch.setattr(
        maintenance.game_cleanup_service,
        "scan",
        AsyncMock(return_value=(False, {}, "bad scan")),
    )
    with pytest.raises(HTTPException, match="bad scan"):
        await maintenance.scan_server_cleanup(3, db, user)

    owner = SimpleNamespace(id=1)
    monkeypatch.setattr(maintenance, "get_server_owner_user", AsyncMock(return_value=owner))
    monkeypatch.setattr(
        maintenance.s3_backup_service,
        "list_backups",
        AsyncMock(return_value=(True, [{"key": "k"}], "")),
    )
    assert (await maintenance.list_server_s3_backups(3, db, user))[0]["key"] == "k"
    monkeypatch.setattr(
        maintenance.s3_backup_service,
        "list_backups",
        AsyncMock(return_value=(False, [], "not configured")),
    )
    with pytest.raises(HTTPException, match="not configured"):
        await maintenance.list_server_s3_backups(3, db, user)


@pytest.mark.asyncio
async def test_maintenance_cpu_disk_deployment_and_confirmation_branches(monkeypatch):
    db = _Db()
    user = SimpleNamespace(id=1)
    server = _server()
    monkeypatch.setattr(maintenance, "get_server_with_permission", AsyncMock(return_value=server))
    ssh = _SSH(commands=[(True, "8\n", "")])
    monkeypatch.setattr(maintenance, "SSHManager", lambda: ssh)
    monkeypatch.setattr("services.ssh_manager.SSHManager", lambda: ssh)
    cpu = await maintenance.get_server_cpu_count(3, db, user)
    assert cpu["cpu_count"] == 8
    ssh.commands = [(True, "not-number", ""), (True, "4", "")]
    assert (await maintenance.get_server_cpu_count(3, db, user))["cpu_count"] == 4
    ssh.commands = [(True, "not-number", ""), (False, "", "bad")]
    assert (await maintenance.get_server_cpu_count(3, db, user))["cpu_count"] == 32
    ssh.connect_result = (False, "offline")
    assert (await maintenance.get_server_cpu_count(3, db, user))["cpu_count"] == 32

    monkeypatch.setattr(
        "services.system_info_helper.system_info_helper.get_disk_space",
        AsyncMock(side_effect=[{"free": 1}, None]),
    )
    assert (await maintenance.get_server_disk_space(3, False, db=db, current_user=user))["success"]
    assert not (await maintenance.get_server_disk_space(3, False, db=db, current_user=user))["success"]

    ssh = _SSH(commands=[(True, "exists", "")])
    monkeypatch.setattr(maintenance, "SSHManager", lambda: ssh)
    deployed = await maintenance.check_server_deployment(3, db, user)
    assert deployed["is_deployed"] is True
    ssh.connect_result = (False, "offline")
    assert (await maintenance.check_server_deployment(3, db, user))["error"] is True

    monkeypatch.setattr(maintenance.redis_manager, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(maintenance.redis_manager, "set_server_status", AsyncMock())
    confirmed = await maintenance.confirm_server_deployment(3, db, user)
    assert confirmed["success"] and confirmed["status"] == "stopped"
    monkeypatch.setattr(maintenance.redis_manager, "get", AsyncMock(return_value="op"))
    with pytest.raises(HTTPException, match="currently in progress"):
        await maintenance.confirm_server_deployment(3, db, user)


@pytest.mark.asyncio
async def test_maintenance_health_and_reconnect_cover_auth_and_duration(monkeypatch):
    db = _Db()
    user = SimpleNamespace(id=1)
    now = datetime.now(UTC)
    server = _server(
        consecutive_ssh_failures=3,
        ssh_health_check_interval_hours=None,
        last_ssh_success=now,
        last_ssh_failure=now,
        last_ssh_health_check=now,
    )
    db.get = AsyncMock(return_value=server)
    health = await maintenance.get_ssh_health_status(3, db, user)
    assert health["offline_duration_estimate"]["hours"] == 6
    assert health["last_ssh_success"]

    monitor = SimpleNamespace(manual_reconnect=AsyncMock(side_effect=[(True, "restored"), (False, "still down")]))
    monkeypatch.setattr("services.ssh_health_monitor.ssh_health_monitor", monitor)
    good = await maintenance.manual_ssh_reconnect(3, db, user)
    bad = await maintenance.manual_ssh_reconnect(3, db, user)
    assert good["success"] is True and bad["success"] is False

    db.get.return_value = None
    with pytest.raises(HTTPException, match="Server not found"):
        await maintenance.get_ssh_health_status(3, db, user)
