"""Focused branch coverage for production-refactor compatibility edges."""

from __future__ import annotations

import asyncio
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes import health as health_routes
from modules.models import PasswordResetToken, Server, User
from services.a2s_cache_service import A2SCacheService
from services.deployment_progress import (
    DeploymentProgressBuffer,
    DeploymentWebSocket,
    _WebSocketSender,
)
from services.steam_inf_service import SteamInfService

a2s_module = import_module("services.a2s_cache_service")
deployment_module = import_module("services.deployment_progress")
steam_inf_module = import_module("services.steam_inf_service")


class _Result:
    def __init__(self, value=None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _Session:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.execute_calls = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement, params=None):
        self.execute_calls.append((statement, params))
        value = self.results.pop(0) if self.results else None
        return _Result(value)

    async def commit(self) -> None:
        self.commits += 1


def _redis_adapter(*, get_result=None, set_error: Exception | None = None):
    set_mock = AsyncMock(side_effect=set_error)
    return SimpleNamespace(
        set=set_mock,
        get=AsyncMock(return_value=get_result),
        mget=AsyncMock(return_value=[]),
    )


@pytest.mark.asyncio
async def test_hashed_identity_lookups_stop_before_legacy_fallback() -> None:
    user = object()
    reset_token = object()
    user_session = _Session(user)
    token_session = _Session(reset_token)

    assert await User.get_by_api_key(user_session, "user-secret") is user
    assert await PasswordResetToken.get_by_token(token_session, "reset-secret") is reset_token

    assert len(user_session.execute_calls) == 1
    assert len(token_session.execute_calls) == 1


@pytest.mark.asyncio
async def test_server_api_key_lookup_supports_digest_and_legacy_rows() -> None:
    digest_match = object()
    digest_session = _Session(digest_match)

    assert await Server.get_by_api_key(digest_session, "new-secret") is digest_match
    assert len(digest_session.execute_calls) == 1

    legacy_match = object()
    legacy_session = _Session(None, legacy_match)

    assert await Server.get_by_api_key(legacy_session, "legacy-secret") is legacy_match
    assert len(legacy_session.execute_calls) == 2
    assert legacy_session.execute_calls[1][1] == {"legacy_api_key": "legacy-secret"}


@pytest.mark.asyncio
async def test_health_helpers_fail_closed_for_resource_and_migration_errors(
    monkeypatch,
) -> None:
    async def broken_probe() -> bool:
        raise ConnectionError("resource unavailable")

    assert await health_routes._probe("database", broken_probe()) is False

    async def broken_migration_status(_engine):
        raise RuntimeError("migration metadata unavailable")

    monkeypatch.setattr(
        "cs2_manager.infrastructure.migrations.get_migration_status",
        broken_migration_status,
    )
    container = SimpleNamespace(database=SimpleNamespace(engine=object()))
    assert await health_routes._migration_is_current(container) is False


def test_runtime_readiness_rejects_missing_or_closing_resources() -> None:
    missing = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                container=SimpleNamespace(http=None, task_supervisor=object()),
            )
        )
    )
    closing = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                container=SimpleNamespace(
                    http=object(),
                    task_supervisor=SimpleNamespace(_closing=True),
                ),
            )
        )
    )

    assert health_routes._runtime_is_ready(missing) is False
    assert health_routes._runtime_is_ready(closing) is False


@pytest.mark.asyncio
async def test_steam_refresh_skips_unhealthy_server() -> None:
    service = SteamInfService()
    service.get_version_from_steam_inf = AsyncMock()
    server = SimpleNamespace(id=7, should_skip_background_checks=lambda: True)

    await service._refresh_server_with_timeout(server)

    service.get_version_from_steam_inf.assert_not_awaited()


@pytest.mark.asyncio
async def test_steam_refresh_reports_success_and_contains_failures() -> None:
    service = SteamInfService()
    server = SimpleNamespace(id=7, should_skip_background_checks=lambda: False)
    service.get_version_from_steam_inf = AsyncMock(return_value=(True, "1.2.3.4"))

    await service._refresh_server_with_timeout(server)

    service.get_version_from_steam_inf.assert_awaited_once_with(server, force_refresh=True)

    service.get_version_from_steam_inf = AsyncMock(side_effect=RuntimeError("SSH failed"))
    await service._refresh_server_with_timeout(server)


@pytest.mark.asyncio
async def test_steam_refresh_timeout_is_contained(monkeypatch) -> None:
    service = SteamInfService()
    service.get_version_from_steam_inf = AsyncMock(return_value=(True, "unused"))
    server = SimpleNamespace(id=9, should_skip_background_checks=lambda: False)

    async def raise_timeout(awaitable, *, timeout):
        assert timeout == 35
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(steam_inf_module.asyncio, "wait_for", raise_timeout)

    await service._refresh_server_with_timeout(server)


@pytest.mark.asyncio
async def test_a2s_steam_cache_and_read_use_injected_redis(monkeypatch) -> None:
    adapter = _redis_adapter(get_result={"version": "1.2.3.4"})
    service = A2SCacheService(redis_adapter=adapter)
    check_version = AsyncMock(
        return_value=(
            True,
            {
                "success": True,
                "required_version": "1.2.3.4",
                "message": "current",
            },
        )
    )
    monkeypatch.setattr(
        "services.steam_api_service.steam_api_service.check_version",
        check_version,
    )

    await service._cache_steam_version()
    cached = await service.get_latest_steam_version()

    adapter.set.assert_awaited_once()
    assert adapter.set.await_args.args[0] == "steam:latest_version"
    adapter.get.assert_awaited_once_with("steam:latest_version")
    assert cached == {"version": "1.2.3.4"}


@pytest.mark.asyncio
async def test_a2s_sweep_contains_unexpected_errors_and_skips_unhealthy_servers(
    monkeypatch,
) -> None:
    service = A2SCacheService(session_factory=lambda: _Session([]))
    monkeypatch.setattr(
        service,
        "_run_server_sweep",
        AsyncMock(side_effect=RuntimeError("database failed")),
    )

    await service._query_all_servers()

    unhealthy = SimpleNamespace(id=13, should_skip_background_checks=lambda: True)
    session = _Session([unhealthy])
    service = A2SCacheService(session_factory=lambda: session)
    query = AsyncMock()
    monkeypatch.setattr(service, "_query_and_cache_server", query)

    await service._query_all_servers()

    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_a2s_error_cache_handles_available_and_failed_redis() -> None:
    available = _redis_adapter()
    await A2SCacheService(redis_adapter=available)._cache_query_error(4, "offline")
    available.set.assert_awaited_once()
    assert available.set.await_args.args[0] == "a2s:server:4"
    assert available.set.await_args.args[1]["error"] == "offline"

    failed = _redis_adapter(set_error=ConnectionError("redis unavailable"))
    await A2SCacheService(redis_adapter=failed)._cache_query_error(5, "timeout")
    failed.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_a2s_success_updates_version_after_network_io(monkeypatch) -> None:
    adapter = _redis_adapter()
    session = _Session()
    service = A2SCacheService(
        redis_adapter=adapter,
        session_factory=lambda: session,
    )
    server = SimpleNamespace(
        id=21,
        name="arena",
        host="192.0.2.21",
        game_port=27015,
        a2s_query_host=None,
        a2s_query_port=None,
        current_game_version="old",
    )
    query_info = AsyncMock(
        return_value=(
            True,
            {
                "version": "raw-version",
                "server_name": "Arena",
                "player_count": 3,
                "max_players": 20,
            },
        )
    )
    query_players = AsyncMock(return_value=(True, [{"name": "player"}]))
    monkeypatch.setattr(a2s_module.a2s_service, "query_server_info", query_info)
    monkeypatch.setattr(a2s_module.a2s_service, "query_players", query_players)
    monkeypatch.setattr(
        "services.steam_api_service.steam_api_service.parse_version_from_a2s",
        lambda _version: "1.2.3.4",
    )

    await service._query_and_cache_server(server)

    adapter.set.assert_awaited_once()
    assert adapter.set.await_args.args[0] == "a2s:server:21"
    assert adapter.set.await_args.args[1]["players"] == [{"name": "player"}]
    assert session.commits == 1
    assert len(session.execute_calls) == 1


@pytest.mark.asyncio
async def test_a2s_query_failure_is_cached_and_legacy_cache_is_normalized(
    monkeypatch,
) -> None:
    service = A2SCacheService(redis_adapter=_redis_adapter(get_result='{"success": true}'))
    server = SimpleNamespace(
        id=22,
        name="offline",
        host="192.0.2.22",
        game_port=27015,
        a2s_query_host=None,
        a2s_query_port=None,
    )
    monkeypatch.setattr(
        a2s_module.a2s_service,
        "query_server_info",
        AsyncMock(side_effect=OSError("UDP failed")),
    )
    cache_error = AsyncMock()
    monkeypatch.setattr(service, "_cache_query_error", cache_error)

    await service._query_and_cache_server(server)
    cached = await service.get_cached_info(22)

    cache_error.assert_awaited_once_with(22, "UDP failed")
    assert cached == {"success": True}
    assert service._normalize_cached_info(23, "{not-json") is None


@pytest.mark.asyncio
async def test_sender_completion_and_send_failure_remove_connection() -> None:
    manager = DeploymentWebSocket()
    websocket = object()
    completed = asyncio.create_task(asyncio.sleep(0))
    await completed
    sender = _WebSocketSender(websocket=websocket)  # type: ignore[arg-type]
    sender.task = completed
    manager._senders[websocket] = sender  # type: ignore[index]
    manager.active_connections[8] = [websocket]  # type: ignore[list-item]

    manager._sender_finished(websocket, 8, completed)  # type: ignore[arg-type]

    assert websocket not in manager._senders
    assert 8 not in manager.active_connections

    class BrokenWebSocket:
        async def send_json(self, _message) -> None:
            raise ConnectionError("client disconnected")

    broken = BrokenWebSocket()
    sender = _WebSocketSender(websocket=broken)  # type: ignore[arg-type]
    sender.queue.append({"type": "status", "message": "starting"})
    sender.ready.set()
    manager._senders[broken] = sender  # type: ignore[index]
    manager.active_connections[9] = [broken]  # type: ignore[list-item]

    await manager._send_loop(9, sender)

    assert broken not in manager._senders
    assert 9 not in manager.active_connections


def test_disconnect_outside_event_loop_still_cancels_sender() -> None:
    class CancellableTask:
        cancelled = False

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancelled = True

    manager = DeploymentWebSocket()
    websocket = object()
    task = CancellableTask()
    sender = _WebSocketSender(websocket=websocket)  # type: ignore[arg-type]
    sender.task = task  # type: ignore[assignment]
    manager._senders[websocket] = sender  # type: ignore[index]
    manager.active_connections[3] = [websocket]  # type: ignore[list-item]

    manager.disconnect(websocket, 3)  # type: ignore[arg-type]

    assert task.cancelled is True
    assert 3 not in manager.active_connections


@pytest.mark.asyncio
async def test_send_ignores_connection_without_live_sender() -> None:
    manager = DeploymentWebSocket()
    websocket = object()
    manager.active_connections[3] = [websocket]  # type: ignore[list-item]

    await manager.send_message(3, {"type": "status", "message": "ignored"})


@pytest.mark.asyncio
async def test_delayed_and_multi_server_progress_flushes_are_cleanup_safe(
    monkeypatch,
) -> None:
    append_batch = AsyncMock(return_value=True)
    monkeypatch.setattr(
        deployment_module.redis_manager,
        "append_deployment_progress_batch",
        append_batch,
    )
    immediate = DeploymentProgressBuffer(flush_interval=0)
    await immediate.append(1, {"type": "output", "message": "first", "timestamp": "now"})
    delayed_task = immediate._flush_tasks[1]

    await delayed_task

    assert 1 not in immediate._flush_tasks
    assert 1 not in immediate._pending

    buffered = DeploymentProgressBuffer(flush_interval=3600)
    await buffered.append(2, {"type": "output", "message": "second", "timestamp": "now"})
    await buffered.append(3, {"type": "output", "message": "third", "timestamp": "now"})

    await buffered.flush_all()
    await buffered.close()

    persisted_server_ids = [call.args[0] for call in append_batch.await_args_list]
    assert persisted_server_ids == [1, 2, 3]


@pytest.mark.asyncio
async def test_deployment_update_and_shutdown_delegate_to_owned_components(
    monkeypatch,
) -> None:
    send_message = AsyncMock()
    append = AsyncMock()
    close = AsyncMock()
    monkeypatch.setattr(deployment_module.deployment_ws, "send_message", send_message)
    monkeypatch.setattr(deployment_module.deployment_progress_buffer, "append", append)
    monkeypatch.setattr(deployment_module.deployment_progress_buffer, "close", close)

    await deployment_module.send_deployment_update(42, "status", "starting")
    await deployment_module.flush_deployment_progress()

    send_message.assert_awaited_once()
    append.assert_awaited_once()
    assert append.await_args.args[0] == 42
    assert append.await_args.args[1]["type"] == "status"
    close.assert_awaited_once_with()
