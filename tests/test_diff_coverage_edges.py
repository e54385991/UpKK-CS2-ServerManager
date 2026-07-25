"""Small edge-case tests for production-refactor diff coverage."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routes import gmail_oauth, public, server_status
from api.routes.actions.status import SSHServerSnapshot
from modules import AuthType, ServerStatus
from modules.logging_config import JSONFormatter
from services.game_session import cs2_startup_parameters
from services.redis_manager import RedisManager


def _request_state(**values):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**values)))


def test_ssh_snapshot_auth_properties_cover_both_modes():
    password = SSHServerSnapshot(
        id=1,
        host="127.0.0.1",
        ssh_port=22,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        credential_revision=0,
        ssh_password="secret",
        ssh_key_path=None,
        ssh_host_key_algorithm="ssh-ed25519",
        ssh_host_key_fingerprint="SHA256:test",
    )

    assert password.is_password_auth is True
    assert password.is_key_auth is False
    key_file = replace(password, auth_type=AuthType.KEY_FILE, ssh_password=None)
    assert key_file.is_password_auth is False
    assert key_file.is_key_auth is True


def test_gmail_oauth_resource_and_settings_validation_fail_closed():
    incomplete_redis = SimpleNamespace(client=SimpleNamespace(set=lambda *_a, **_k: None))
    request = _request_state(container=SimpleNamespace(redis=incomplete_redis))
    with pytest.raises(gmail_oauth.OAuthStateStoreUnavailable):
        gmail_oauth._oauth_state_redis(request)

    with pytest.raises(HTTPException) as exc_info:
        gmail_oauth._oauth_redirect_uri(_request_state())
    assert exc_info.value.status_code == 500


def test_json_formatter_includes_exception_and_stack_information():
    try:
        raise RuntimeError("formatter failure")
    except RuntimeError:
        record = logging.LogRecord(
            "coverage",
            logging.ERROR,
            __file__,
            1,
            "failed",
            (),
            sys.exc_info(),
        )
    record.stack_info = "stack details"

    payload = json.loads(JSONFormatter().format(record))

    assert "RuntimeError: formatter failure" in payload["exception"]
    assert "stack details" in payload["stack"]


def test_game_session_explicit_ports_tv_and_invalid_token_branches():
    rendered = cs2_startup_parameters(
        game_port=27015,
        client_port=27025,
        default_map="de_dust2",
        max_players=20,
        server_name="Test",
        tv_enable=True,
        tv_port=27030,
    )
    assert "+clientport 27025" in rendered
    assert "+tv_enable 1" in rendered
    assert "+tv_port 27030" in rendered

    with pytest.raises(ValueError, match="alphanumeric"):
        cs2_startup_parameters(
            game_port=27015,
            default_map="de_dust2",
            max_players=20,
            server_name="Test",
            steam_account_token="not-valid!",
        )


class _FailingRedisClient:
    async def setex(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def get(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def delete(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def ping(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def lrange(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def lrem(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def eval(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")


def _failing_redis_manager() -> RedisManager:
    manager = object.__new__(RedisManager)
    manager._coordination_retry_after = 0.0
    manager.client = _FailingRedisClient()
    return manager


@pytest.mark.asyncio
async def test_redis_cache_helpers_return_safe_values_on_transport_errors(monkeypatch):
    manager = _failing_redis_manager()

    assert await manager.set("key", {"value": 1}) is False
    assert await manager.get("key") is None
    assert await manager.delete("key") is False
    assert await manager.ping() is False
    assert await manager.get_initialized_servers(7) == []
    assert await manager.delete_initialized_server(7, "server-key") is False
    assert await manager.get_deployment_progress(7) == []
    assert (
        await manager.set_batch_action_status(
            "batch",
            7,
            "failed",
            user_id=9,
        )
        is False
    )

    async def fail_pattern(_pattern):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(manager, "delete_by_pattern", fail_pattern)
    assert await manager.clear_server_cache(7) is False


class _QueryResult:
    def __init__(self, row=None):
        self._row = row

    def one_or_none(self):
        return self._row


class _SequenceSession:
    def __init__(self, rows):
        self.rows = list(rows)

    async def execute(self, _statement):
        return _QueryResult(self.rows.pop(0))


def _agent_row(**overrides):
    values = {
        "id": 7,
        "name": "agent-seven",
        "status": ServerStatus.STOPPED,
        "game_port": 27015,
        "default_map": "de_dust2",
        "max_players": 20,
        "game_mode": "competitive",
        "game_type": "0",
    }
    values.update(overrides)
    return SimpleNamespace(_mapping=values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [_agent_row(_api_key_hash="unexpected")],
        [None, None],
        [None, _agent_row(_legacy_api_key=None)],
        [None, _agent_row(_legacy_api_key="different-key")],
    ],
)
async def test_server_agent_lookup_rejects_all_invalid_storage_variants(rows):
    principal = await server_status._find_server_agent(
        _SequenceSession(rows),
        "supplied-key",
        "test-hmac-key-with-at-least-32-bytes",
    )
    assert principal is None


class _WriteSession:
    def __init__(self):
        self.added = []
        self.statements = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def execute(self, statement):
        self.statements.append(statement)
        return _QueryResult()

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "initial_status", "expected_status"),
    [
        ("crash", ServerStatus.RUNNING, ServerStatus.ERROR),
        ("restart", ServerStatus.STOPPED, ServerStatus.RUNNING),
        ("shutdown", ServerStatus.RUNNING, ServerStatus.STOPPED),
        ("crash_limit_reached", ServerStatus.RUNNING, ServerStatus.STOPPED),
    ],
)
async def test_server_status_report_event_transitions(
    event_type,
    initial_status,
    expected_status,
):
    principal = server_status.ServerAgentPrincipal(
        id=7,
        name="agent-seven",
        status=initial_status,
        game_port=27015,
        default_map="de_dust2",
        max_players=20,
        game_mode="competitive",
        game_type="0",
    )
    report = server_status.ServerStatusReport(
        event_type=event_type,
        message="event",
        restart_count=3,
        crash_details="details" if event_type == "crash" else None,
    )
    db = _WriteSession()

    response = await server_status.report_server_status(7, report, principal, db)

    assert response["current_status"] == expected_status.value
    assert db.commits == 1
    assert db.added
    assert db.statements


@pytest.mark.asyncio
async def test_server_pool_stats_fails_closed_without_an_app_pool():
    db = _WriteSession()
    request = _request_state(container=SimpleNamespace(ssh_pool=None))

    with pytest.raises(HTTPException) as exc_info:
        await server_status.get_ssh_pool_stats(request, SimpleNamespace(), db)

    assert exc_info.value.status_code == 503
    assert db.commits == 1


@pytest.mark.asyncio
async def test_public_a2s_cache_returns_compatibility_error_when_resources_are_missing():
    principal = SimpleNamespace(id=7, is_admin=False)
    request = _request_state(container=SimpleNamespace())

    response = await public.get_user_servers_a2s_cache(
        request=request,
        uow=SimpleNamespace(session=object()),
        current_user=principal,
    )

    assert response["servers"] == {}
    assert response["error"] == "Cache unavailable"
