"""行为测试：服务器 CRUD 的远程校验、去重和代理默认值分支。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest
from fastapi import HTTPException

from api.routes.servers import crud
from modules import Server, ServerCreate, SystemSettings
from services.host_initialization import HostDependencyResult


class _Conn:
    def __init__(self, statuses=(0, 0, 0)):
        self.statuses = list(statuses)
        self.commands = []
        self.closed = False

    async def run(self, command, check=False):
        self.commands.append(command)
        return SimpleNamespace(exit_status=self.statuses.pop(0))

    def close(self):
        self.closed = True


class _DB:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []
        self.commits = 0
        self.flushed = 0

    async def execute(self, _statement):
        return self.results.pop(0)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        self.flushed += 1
        for item in self.added:
            if isinstance(item, Server) and item.id is None:
                item.id = 17

    async def refresh(self, _item):
        return None

    async def delete(self, item):
        self.added.append(("deleted", item))


def _create(**overrides):
    values = {
        "name": "alpha",
        "host": "host.example",
        "ssh_user": "steam",
        "ssh_password": "password",
        "sudo_password": "sudo",
        "game_directory": "/srv/cs2",
        "captcha_token": "token",
        "captcha_code": "AB12",
    }
    values.update(overrides)
    return ServerCreate(**values)


@pytest.mark.asyncio
async def test_validate_connection_requires_password_and_maps_remote_failures(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await crud._validate_server_connection(SimpleNamespace(ssh_password=""))
    assert exc.value.status_code == 400

    conn = _Conn()
    monkeypatch.setattr(crud.asyncssh, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(
        crud,
        "ensure_steamcmd_packages",
        AsyncMock(
            return_value=HostDependencyResult(
                success=True,
                architecture_supported=True,
                architecture="amd64",
                missing_before=(),
                missing_after=(),
                installed=True,
                privilege="sudo",
                message="ok",
                manual_install_command=None,
                logs=(),
                apt_mirror="official",
            )
        ),
    )
    result = await crud._validate_server_connection(_create())
    assert result.success is True
    assert conn.closed is True
    assert conn.commands[1].startswith("mkdir -p")

    for error, status_code in (
        (asyncio.TimeoutError(), 504),
        (asyncssh.PermissionDenied("denied"), 400),
        (asyncssh.ConnectionLost("lost"), 400),
        (asyncssh.Error(0, "failed"), 400),
    ):
        monkeypatch.setattr(crud.asyncssh, "connect", AsyncMock(side_effect=error))
        with pytest.raises(HTTPException) as exc:
            await crud._validate_server_connection(_create())
        assert exc.value.status_code == status_code


@pytest.mark.asyncio
async def test_validate_connection_maps_command_and_architecture_errors(monkeypatch):
    for statuses in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
        conn = _Conn(statuses)
        monkeypatch.setattr(crud.asyncssh, "connect", AsyncMock(return_value=conn))
        with pytest.raises(HTTPException) as exc:
            await crud._validate_server_connection(_create())
        assert exc.value.status_code == 400

    conn = _Conn()
    monkeypatch.setattr(crud.asyncssh, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(
        crud,
        "ensure_steamcmd_packages",
        AsyncMock(
            return_value=HostDependencyResult(
                success=False,
                architecture_supported=False,
                architecture="arm64",
                missing_before=(),
                missing_after=(),
                installed=False,
                privilege="none",
                message="unsupported",
                manual_install_command=None,
                logs=(),
            )
        ),
    )
    with pytest.raises(HTTPException, match="unsupported"):
        await crud._validate_server_connection(_create())

    monkeypatch.setattr(crud.asyncssh, "connect", AsyncMock(side_effect=RuntimeError("boom")))
    with pytest.raises(HTTPException, match="boom"):
        await crud._validate_server_connection(_create())


@pytest.mark.asyncio
async def test_create_record_captcha_duplicates_defaults_and_audit(monkeypatch):
    user = SimpleNamespace(id=8)
    request = SimpleNamespace()
    monkeypatch.setattr(crud, "require_captcha", AsyncMock())
    monkeypatch.setattr(crud.Server, "get_by_name_and_user", AsyncMock(return_value=None))
    monkeypatch.setattr(crud.Server, "get_by_host_directory_and_user", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "inherit_global_discord_binding", AsyncMock())
    monkeypatch.setattr(crud, "record_audit_event", AsyncMock())
    monkeypatch.setattr(crud, "generate_api_key", lambda: "api-key")
    monkeypatch.setattr(
        crud.SystemSettings,
        "get_or_create_settings",
        AsyncMock(
            return_value=SystemSettings(
                default_proxy_mode="github_url", github_proxy_url="https://proxy"
            )
        ),
    )
    host_result = HostDependencyResult(
        success=True,
        architecture_supported=True,
        architecture="amd64",
        missing_before=(),
        missing_after=(),
        installed=True,
        privilege="root",
        message="ok",
        manual_install_command=None,
        logs=(),
        apt_mirror="ustc",
    )
    monkeypatch.setattr(crud, "_validate_server_connection", AsyncMock(return_value=host_result))
    db = _DB()
    server = await crud.create_server_record(_create(), db, user, request)
    assert server.id == 17
    assert server.github_proxy == "https://proxy"
    assert server.apt_mirror == "ustc"
    assert any(isinstance(item, crud.ServerAgentPolicy) for item in db.added)
    assert crud.record_audit_event.await_count == 1

    monkeypatch.setattr(
        crud.Server, "get_by_name_and_user", AsyncMock(return_value=SimpleNamespace())
    )
    with pytest.raises(HTTPException) as exc:
        await crud.create_server_record(_create(), _DB(), user, request)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await crud.create_server_record(_create(), _DB(), user, request, source_server_id=9)
    assert exc.value.status_code == 409

    monkeypatch.setattr(crud.Server, "get_by_name_and_user", AsyncMock(return_value=None))
    monkeypatch.setattr(
        crud.Server, "get_by_host_directory_and_user", AsyncMock(return_value=SimpleNamespace())
    )
    with pytest.raises(HTTPException) as exc:
        await crud.create_server_record(_create(), _DB(), user, request)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_record_skip_validation_proxy_modes_and_listing(monkeypatch):
    user = SimpleNamespace(id=8)
    monkeypatch.setattr(crud, "require_captcha", AsyncMock())
    monkeypatch.setattr(crud.Server, "get_by_name_and_user", AsyncMock(return_value=None))
    monkeypatch.setattr(crud.Server, "get_by_host_directory_and_user", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "inherit_global_discord_binding", AsyncMock())
    monkeypatch.setattr(crud, "record_audit_event", AsyncMock())
    monkeypatch.setattr(crud, "generate_api_key", lambda: "key")
    monkeypatch.setattr(crud, "DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS", [])
    for mode, expected in (("panel", (True, None)), ("direct", (False, None))):
        monkeypatch.setattr(
            crud.SystemSettings,
            "get_or_create_settings",
            AsyncMock(return_value=SystemSettings(default_proxy_mode=mode)),
        )
        server = await crud.create_server_record(
            _create(), _DB(), user, SimpleNamespace(), skip_host_initialization=True
        )
        assert (server.use_panel_proxy, server.github_proxy) == expected

    monkeypatch.setattr(
        crud.SystemSettings,
        "get_or_create_settings",
        AsyncMock(
            return_value=SystemSettings(default_proxy_mode="github_url", github_proxy_url="x")
        ),
    )
    server = await crud.create_server_record(
        _create(use_panel_proxy=True),
        _DB(),
        user,
        SimpleNamespace(),
        skip_host_initialization=True,
        apply_system_defaults=False,
        source_server_id=3,
    )
    assert server.use_panel_proxy is True

    monkeypatch.setattr(crud.Server, "get_all_by_user", AsyncMock(return_value=[server]))
    assert await crud.list_servers(0, 10, db=_DB(), current_user=user) == [server]
    monkeypatch.setattr(crud.Server, "get_all", AsyncMock(return_value=[]))
    assert await crud.list_all_servers_admin(0, 10, db=_DB(), current_user=user) == []


@pytest.mark.asyncio
async def test_update_apply_delete_server_and_admin_listing(monkeypatch):
    user = SimpleNamespace(id=8, is_admin=False)
    server = Server(
        id=4,
        user_id=8,
        name="old",
        host="host",
        ssh_user="steam",
        enable_panel_monitoring=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(crud, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(crud, "record_audit_event", AsyncMock())
    monkeypatch.setattr(crud.redis_manager, "clear_server_cache", AsyncMock())
    monkeypatch.setattr("services.server_monitor.server_monitor.start_monitoring", lambda *_a: None)
    db = _DB()
    from modules import ServerUpdate

    response = await crud.update_server(
        4,
        ServerUpdate(server_name="new", enable_panel_monitoring=True),
        db,
        user,
        SimpleNamespace(),
    )
    assert response.restart_required is False
    assert server.enable_panel_monitoring is True

    monkeypatch.setattr(crud.SystemSettings, "get_settings", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await crud.apply_system_defaults_to_server(4, db, user)
    assert exc.value.status_code == 404
    for mode in ("panel", "github_url", "direct"):
        settings = SystemSettings(default_proxy_mode=mode, github_proxy_url="proxy")
        monkeypatch.setattr(crud.SystemSettings, "get_settings", AsyncMock(return_value=settings))
        result = await crud.apply_system_defaults_to_server(4, db, user)
        assert result is server

    monkeypatch.setattr(crud, "get_server_with_permission", AsyncMock(return_value=server))
    assert await crud.delete_server(4, db, user, SimpleNamespace()) is None
    assert crud.redis_manager.clear_server_cache.await_count >= 4

    rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [server]))
    monkeypatch.setattr(crud.Server, "get_all", AsyncMock(return_value=[server]))
    admin_db = _DB(rows)
    result = await crud.list_all_servers_admin(
        0, 10, db=admin_db, current_user=SimpleNamespace(id=1, is_admin=True)
    )
    assert result[0].user is None
