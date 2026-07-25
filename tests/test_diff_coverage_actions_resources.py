"""Focused diff coverage for action and plugin application resources."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.routes import github_plugins, plugin_configs, plugin_market
from api.routes.actions import common as action_common
from api.routes.actions import console as console_routes
from api.routes.actions import deployment
from api.routes.servers import configuration
from modules import (
    AuthType,
    PluginConfigSource,
    Server,
    ServerAction,
    ServerStatus,
    User,
)
from services import plugin_auto_update_service


def _server(**overrides: Any) -> Server:
    values: dict[str, Any] = {
        "id": 17,
        "user_id": 7,
        "name": "Resource Server",
        "host": "192.0.2.17",
        "ssh_user": "steam",
        "auth_type": AuthType.PASSWORD,
        "status": ServerStatus.STOPPED,
    }
    values.update(overrides)
    return Server(**values)


def _user(**overrides: Any) -> User:
    values: dict[str, Any] = {
        "id": 7,
        "username": "resource-owner",
        "email": "resource-owner@example.test",
        "hashed_password": "not-used",
        "is_active": True,
        "is_admin": True,
    }
    values.update(overrides)
    return User(**values)


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _Rows:
        return self

    def all(self) -> list[object]:
        return self._rows


class _Session:
    def __init__(
        self,
        *,
        server: Server | None = None,
        owner: User | None = None,
        rows: list[object] | None = None,
        active: dict[str, int] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.server = server
        self.owner = owner
        self.rows = rows or []
        self.active = active
        self.events = events
        self.added: list[object] = []
        self.commit_count = 0

    async def __aenter__(self) -> _Session:
        if self.active is not None:
            self.active["count"] += 1
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self.active is not None:
            self.active["count"] -= 1

    async def get(self, model: type[object], _record_id: int) -> object | None:
        if model is User:
            return self.owner
        if model is Server:
            return self.server
        return None

    async def execute(self, _statement: object) -> _Rows:
        return _Rows(self.rows)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_count += 1
        if self.events is not None:
            self.events.append("commit")

    async def refresh(self, _value: object) -> None:
        return None


class _WebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.messages: list[dict[str, object]] = []
        self.close_count = 0

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    async def close(self, *_args: object, **_kwargs: object) -> None:
        self.close_count += 1


def _application_http(get_result: tuple[bool, object, str | None] | None = None):
    @asynccontextmanager
    async def borrow_client():
        yield object()

    return SimpleNamespace(
        get=AsyncMock(return_value=get_result or (True, {}, None)),
        post=AsyncMock(),
        borrow_client=borrow_client,
        request=AsyncMock(),
        is_closed=False,
    )


def test_request_background_sessions_fail_closed_without_app_database() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace(database=None)))
    )

    with pytest.raises(HTTPException) as exc_info:
        action_common._request_session_factory(request)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Background database sessions are unavailable"


@pytest.mark.asyncio
async def test_admin_background_action_uses_snapshot_and_injected_ssh(monkeypatch) -> None:
    server = _server()
    active = {"count": 0}
    sessions: list[_Session] = []

    def session_factory() -> _Session:
        session = _Session(server=server, active=active)
        sessions.append(session)
        return session

    async def stop_server(received: Server) -> tuple[bool, str]:
        assert active["count"] == 0
        assert received is not server
        assert received.id == server.id
        return True, "stopped"

    manager = SimpleNamespace(stop_server=AsyncMock(side_effect=stop_server))
    manager_factory = Mock(return_value=manager)
    get_by_id = AsyncMock(return_value=server)
    monkeypatch.setattr(action_common.Server, "get_by_id", get_by_id)
    monkeypatch.setattr(action_common.redis_manager, "set_batch_action_status", AsyncMock())
    monkeypatch.setattr(action_common, "send_discord_action_notification", AsyncMock())

    await action_common.execute_single_server_action(
        server.id or 17,
        "stop",
        user_id=7,
        is_admin=True,
        batch_id="admin-stop",
        session_factory=session_factory,
        ssh_manager_factory=manager_factory,
    )

    get_by_id.assert_awaited_once()
    manager_factory.assert_called_once_with()
    manager.stop_server.assert_awaited_once()
    assert len(sessions) == 2
    assert active["count"] == 0


@pytest.mark.asyncio
async def test_admin_plugin_worker_tracks_all_framework_metadata(monkeypatch) -> None:
    server = _server()
    owner = _user()
    active = {"count": 0}
    sessions: list[_Session] = []

    def session_factory() -> _Session:
        session = _Session(server=server, owner=owner, active=active)
        sessions.append(session)
        return session

    async def install(received: Server) -> tuple[bool, str]:
        assert active["count"] == 0
        assert received is not server
        return True, "installed"

    http_resource = object()
    manager = SimpleNamespace(
        http_resource=http_resource,
        install_counterstrikesharp=AsyncMock(side_effect=install),
        install_cs2fixes=AsyncMock(side_effect=install),
    )
    manager_factory = Mock(return_value=manager)
    get_by_id = AsyncMock(return_value=server)
    record_framework = AsyncMock()
    record_known = AsyncMock()
    monkeypatch.setattr(action_common.Server, "get_by_id", get_by_id)
    monkeypatch.setattr(action_common.redis_manager, "set_batch_action_status", AsyncMock())
    monkeypatch.setattr(action_common, "send_discord_action_notification", AsyncMock())
    monkeypatch.setattr(
        plugin_auto_update_service,
        "record_framework_installation",
        record_framework,
    )
    monkeypatch.setattr(
        plugin_auto_update_service,
        "record_known_github_installation",
        record_known,
    )

    await action_common.execute_single_server_plugins(
        server.id or 17,
        ["counterstrikesharp", "cs2fixes"],
        user_id=owner.id or 7,
        is_admin=True,
        batch_id="admin-plugins",
        session_factory=session_factory,
        ssh_manager_factory=manager_factory,
    )

    get_by_id.assert_awaited_once()
    manager_factory.assert_called_once_with()
    assert [awaited.args[2] for awaited in record_framework.await_args_list] == [
        "counterstrikesharp",
        "metamod",
        "metamod",
    ]
    assert all(
        awaited.kwargs["http_resource"] is http_resource
        and awaited.kwargs["ssh_manager_factory"] is manager_factory
        for awaited in record_framework.await_args_list
    )
    record_known.assert_awaited_once()
    assert record_known.await_args.kwargs["http_resource"] is http_resource
    assert len(sessions) == 3
    assert active["count"] == 0


@pytest.mark.asyncio
async def test_game_console_closes_when_ssh_has_no_session(monkeypatch) -> None:
    websocket = _WebSocket()
    server = SimpleNamespace(id=17, host="192.0.2.17", session_manager="tmux")
    manager = SimpleNamespace(
        conn=None,
        connect=AsyncMock(return_value=(True, "connected")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(
        console_routes,
        "authenticate_websocket",
        AsyncMock(return_value=(SimpleNamespace(id=7), server)),
    )
    monkeypatch.setattr(console_routes, "get_ssh_manager", lambda _websocket: manager)

    await console_routes.game_console_websocket(websocket, 17)

    assert websocket.accepted is True
    assert websocket.messages == [
        {"type": "error", "message": "SSH connection did not provide a session"}
    ]
    assert websocket.close_count == 1
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_deployment_releases_ssh_after_connect_error(monkeypatch) -> None:
    server = _server()
    manager = SimpleNamespace(
        connect=AsyncMock(side_effect=RuntimeError("unreachable")),
        disconnect=AsyncMock(),
    )
    db = _Session(server=server)
    monkeypatch.setattr(
        deployment,
        "get_server_and_verify_ownership",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(deployment.redis_manager, "get", AsyncMock(return_value="locked"))
    monkeypatch.setattr(deployment.redis_manager, "delete", AsyncMock())
    monkeypatch.setattr(deployment.redis_manager, "clear_deployment_progress", AsyncMock())

    response = await deployment.cancel_deployment(
        server.id or 17,
        db=db,
        current_user=_user(),
        ssh_manager=manager,
    )

    assert response.status_code == 200
    manager.disconnect.assert_awaited_once()
    deployment.redis_manager.delete.assert_awaited_once_with("deployment_lock:17")
    deployment.redis_manager.clear_deployment_progress.assert_awaited_once_with(17)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "frameworks", "known_count"),
    (
        ("install_counterstrikesharp", ["counterstrikesharp", "metamod"], 0),
        ("install_cs2fixes", ["metamod"], 1),
    ),
)
async def test_successful_plugin_action_tracks_with_bound_resources(
    monkeypatch,
    action: str,
    frameworks: list[str],
    known_count: int,
) -> None:
    server = _server()
    owner = _user()
    db = _Session(server=server, owner=owner)
    http_resource = object()
    pool = object()
    manager = SimpleNamespace(
        connection_pool=pool,
        http_resource=http_resource,
        install_counterstrikesharp=AsyncMock(return_value=(True, "installed")),
        install_cs2fixes=AsyncMock(return_value=(True, "installed")),
        disconnect=AsyncMock(),
    )
    record_framework = AsyncMock()
    record_known = AsyncMock()
    monkeypatch.setattr(deployment.redis_manager, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(deployment.redis_manager, "clear_deployment_progress", AsyncMock())
    monkeypatch.setattr(deployment.redis_manager, "set_server_status", AsyncMock())
    monkeypatch.setattr(deployment, "send_deployment_update", AsyncMock())
    monkeypatch.setattr(deployment, "send_discord_action_notification", AsyncMock())
    monkeypatch.setattr(
        plugin_auto_update_service,
        "record_framework_installation",
        record_framework,
    )
    monkeypatch.setattr(
        plugin_auto_update_service,
        "record_known_github_installation",
        record_known,
    )

    response = await deployment.server_action(
        server.id or 17,
        ServerAction(action=action),
        http_request=SimpleNamespace(),
        db=db,
        current_user=owner,
        locked_server=server,
        s3_service=SimpleNamespace(),
        ssh_manager=manager,
    )

    assert response.success is True
    assert [awaited.args[2] for awaited in record_framework.await_args_list] == frameworks
    assert record_known.await_count == known_count
    for awaited in record_framework.await_args_list:
        assert awaited.kwargs["http_resource"] is http_resource
        factory = awaited.kwargs["ssh_manager_factory"]
        closure = getattr(factory, "__closure__", ())
        assert closure
    if known_count:
        assert record_known.await_args.kwargs["http_resource"] is http_resource
    manager.disconnect.assert_awaited_once()


def test_plugin_config_detaches_real_server_models() -> None:
    server = _server()

    snapshot = plugin_configs._detached_server(server)

    assert isinstance(snapshot, Server)
    assert snapshot is not server
    assert snapshot.id == server.id


@pytest.mark.asyncio
async def test_plugin_config_connect_preserves_error_when_cleanup_fails() -> None:
    connect_error = RuntimeError("connect failed")
    manager = SimpleNamespace(
        connect=AsyncMock(side_effect=connect_error),
        disconnect=AsyncMock(side_effect=RuntimeError("cleanup failed")),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await plugin_configs._connect(_server(), manager)

    assert exc_info.value is connect_error
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_config_rejected_connect_preserves_502_when_cleanup_fails() -> None:
    manager = SimpleNamespace(
        connect=AsyncMock(return_value=(False, "denied")),
        disconnect=AsyncMock(side_effect=RuntimeError("cleanup failed")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await plugin_configs._connect(_server(), manager)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "SSH connection failed: denied"
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_config_list_sources_uses_detached_server(monkeypatch) -> None:
    server = _server()
    db = _Session(rows=[])
    get_server = AsyncMock(return_value=server)
    monkeypatch.setattr(plugin_configs, "get_server_with_permission", get_server)

    response = await plugin_configs.list_sources(
        server.id or 17,
        db=db,
        current_user=_user(),
    )

    assert response == {"game_directory": server.game_directory, "sources": []}
    get_server.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_config_browse_uses_injected_ssh(monkeypatch) -> None:
    server = _server()
    manager = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "connected")),
        disconnect=AsyncMock(),
    )
    browse = AsyncMock(return_value=[])
    monkeypatch.setattr(
        plugin_configs,
        "get_server_with_permission",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(plugin_configs, "browse_directory", browse)

    response = await plugin_configs.browse_source_path(
        server.id or 17,
        path=".",
        db=_Session(),
        current_user=_user(),
        ssh_manager=manager,
    )

    assert response == {"path": ".", "items": []}
    manager.connect.assert_awaited_once()
    browse.assert_awaited_once()
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_config_file_commits_before_remote_io(monkeypatch) -> None:
    server = _server()
    source = PluginConfigSource(
        id=4,
        server_id=server.id or 17,
        relative_path="cfg/server.cfg",
        path_hash=plugin_configs.path_hash("cfg/server.cfg"),
        source_type="file",
    )
    events: list[str] = []
    db = _Session(events=events)

    async def connect(_server: Server) -> tuple[bool, str]:
        events.append("connect")
        return True, "connected"

    manager = SimpleNamespace(
        connect=AsyncMock(side_effect=connect),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(
        plugin_configs,
        "get_server_with_permission",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(plugin_configs, "_source_for_server", AsyncMock(return_value=source))
    monkeypatch.setattr(
        plugin_configs, "read_text_file", AsyncMock(return_value='hostname "test"\n')
    )

    response = await plugin_configs.get_config_file(
        server.id or 17,
        source.id or 4,
        "cfg/server.cfg",
        db=db,
        current_user=_user(),
        ssh_manager=manager,
    )

    assert events[:2] == ["commit", "connect"]
    assert response["path"] == "cfg/server.cfg"
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_market_direct_facades_and_missing_user_id_fail_closed(
    monkeypatch,
) -> None:
    legacy_ssh = SimpleNamespace(name="legacy")
    monkeypatch.setattr(plugin_market, "SSHManager", lambda: legacy_ssh)

    assert plugin_market._coerce_ssh_manager(plugin_market._DIRECT_SSH_MANAGER) is legacy_ssh
    with pytest.raises(HTTPException) as ssh_error:
        plugin_market._coerce_ssh_manager(object())
    assert ssh_error.value.status_code == 503

    assert (
        plugin_market._coerce_application_http(plugin_market._DIRECT_APPLICATION_HTTP)
        is plugin_market.http_helper
    )
    with pytest.raises(HTTPException) as http_error:
        plugin_market._coerce_application_http(object())
    assert http_error.value.status_code == 503

    with pytest.raises(HTTPException) as user_error:
        await plugin_market.get_server_for_user(
            17,
            db=SimpleNamespace(),
            current_user=SimpleNamespace(id=None, is_admin=False),
        )
    assert user_error.value.status_code == 404


@pytest.mark.asyncio
async def test_plugin_archive_analysis_uses_injected_http(monkeypatch) -> None:
    server = _server(github_proxy="http://proxy.example.test:8080")
    owner = _user()
    db = _Session()
    http_resource = _application_http(
        (
            True,
            {
                "assets": [
                    {
                        "name": "resource-plugin-linux.zip",
                        "browser_download_url": "https://downloads.example.test/plugin.zip",
                    }
                ]
            },
            None,
        )
    )
    ssh_manager = SimpleNamespace(disconnect=AsyncMock())
    expected = SimpleNamespace(success=True)
    monkeypatch.setattr(
        plugin_market.MarketPlugin,
        "get_by_id",
        AsyncMock(
            return_value=SimpleNamespace(github_url="https://github.com/example/resource-plugin")
        ),
    )
    monkeypatch.setattr(plugin_market, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(
        plugin_market, "get_effective_github_token", AsyncMock(return_value="token")
    )
    analyze = AsyncMock(return_value=expected)
    monkeypatch.setattr(github_plugins, "analyze_archive", analyze)

    result = await plugin_market.analyze_plugin_archive(
        3,
        server_id=server.id or 17,
        download_url=None,
        db=db,
        current_user=owner,
        http_resource=http_resource,
        ssh_manager=ssh_manager,
    )

    assert result is expected
    http_resource.get.assert_awaited_once()
    assert http_resource.get.await_args.kwargs["proxy"] == server.github_proxy
    analyze.assert_awaited_once()
    assert analyze.await_args.kwargs["download_url"] == "https://downloads.example.test/plugin.zip"
    assert analyze.await_args.kwargs["ssh_manager"] is ssh_manager


def test_discord_service_supports_direct_and_app_owned_http() -> None:
    assert (
        configuration._discord_service(configuration._DIRECT_DISCORD_HTTP)
        is configuration.discord_notification_service
    )

    http_resource = _application_http()
    service = configuration._discord_service(http_resource)
    assert service._http is http_resource

    with pytest.raises(HTTPException) as exc_info:
        configuration._discord_service(object())
    assert exc_info.value.status_code == 503
