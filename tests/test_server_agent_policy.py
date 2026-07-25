from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Header, status

from api.dependencies import get_admin_principal
from api.routes import server_status
from cs2_manager.core import Principal
from cs2_manager.infrastructure.credentials import hash_token
from modules import ServerStatus, get_db
from modules.auth import get_current_principal

AGENT_API_KEY = "A" * 64
TOKEN_HASH_KEY = "server-agent-test-hmac-key-with-more-than-32-bytes"


class _Result:
    def __init__(self, row=None):
        self._row = row

    def one_or_none(self):
        return self._row


def _agent_row(**overrides):
    values = {
        "id": 7,
        "name": "agent-seven",
        "status": ServerStatus.STOPPED,
        "game_port": 27015,
        "default_map": "de_ancient",
        "max_players": 24,
        "game_mode": "competitive",
        "game_type": "0",
    }
    values.update(overrides)
    return SimpleNamespace(_mapping=values)


class _QuerySession:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.results.pop(0))


@pytest.mark.asyncio
async def test_server_agent_lookup_uses_hmac_index_and_selects_no_credentials():
    digest = hash_token(AGENT_API_KEY, TOKEN_HASH_KEY)
    row = _agent_row(_api_key_hash=digest)
    db = _QuerySession([row])

    principal = await server_status._find_server_agent(db, AGENT_API_KEY, TOKEN_HASH_KEY)

    assert principal == server_status.ServerAgentPrincipal(
        id=7,
        name="agent-seven",
        status=ServerStatus.STOPPED,
        game_port=27015,
        default_map="de_ancient",
        max_players=24,
        game_mode="competitive",
        game_type="0",
    )
    assert len(db.statements) == 1
    statement = db.statements[0]
    assert digest in statement.compile().params.values()
    selected_sql = str(statement)
    for secret_column in (
        "servers.api_key,",
        "servers.ssh_password",
        "servers.sudo_password",
        "servers.server_password",
        "servers.rcon_password",
        "servers.steam_account_token",
        "servers.discord_webhook_url",
    ):
        assert secret_column not in selected_sql
    assert not hasattr(principal, "api_key")
    assert not hasattr(principal, "rcon_password")


@pytest.mark.asyncio
async def test_server_agent_legacy_lookup_is_limited_and_hmac_verified():
    legacy_row = _agent_row(_legacy_api_key=AGENT_API_KEY)
    db = _QuerySession([None, legacy_row])

    principal = await server_status._find_server_agent(db, AGENT_API_KEY, TOKEN_HASH_KEY)

    assert principal is not None
    assert principal.id == 7
    assert len(db.statements) == 2
    fallback_sql = str(db.statements[1])
    assert "servers.api_key_hash IS NULL" in fallback_sql
    assert "servers.api_key =" in fallback_sql
    assert "servers.rcon_password" not in fallback_sql
    assert "servers.ssh_password" not in fallback_sql


class _AuthSessionContext:
    def __init__(self, tracker):
        self.tracker = tracker
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.closed = True


class _AuthDatabase:
    def __init__(self):
        self.contexts = []

    def session_factory(self):
        context = _AuthSessionContext(self)
        self.contexts.append(context)
        return context


class _WriteSession:
    def __init__(self):
        self.added = []
        self.statements = []
        self.committed = False

    def add(self, value):
        self.added.append(value)

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result()

    async def commit(self):
        self.committed = True


def _server_agent_test_app(monkeypatch):
    auth_database = _AuthDatabase()
    write_session = _WriteSession()
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        TOKEN_HASH_KEY=TOKEN_HASH_KEY,
        SECRET_KEY="unused-fallback",
    )
    app.state.container = SimpleNamespace(
        database=auth_database,
        ssh_pool=None,
    )

    principal = server_status.ServerAgentPrincipal(
        id=7,
        name="agent-seven",
        status=ServerStatus.STOPPED,
        game_port=27015,
        default_map="de_ancient",
        max_players=24,
        game_mode="competitive",
        game_type="0",
    )

    async def find_agent(_db, api_key, token_hash_key):
        assert token_hash_key == TOKEN_HASH_KEY
        return principal if api_key == AGENT_API_KEY else None

    async def write_db():
        yield write_session

    monkeypatch.setattr(server_status, "_find_server_agent", find_agent)
    app.dependency_overrides[get_db] = write_db
    app.include_router(server_status.router)
    return app, auth_database, write_session


@pytest.mark.asyncio
async def test_server_agent_auth_matrix_and_responses_never_echo_secrets(monkeypatch):
    app, auth_database, write_session = _server_agent_test_app(monkeypatch)
    bearer_only = {"Authorization": "Bearer ordinary-user-token"}
    wrong_key = {"X-API-Key": "wrong-agent-key"}
    valid_key = {"X-API-Key": AGENT_API_KEY}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        no_key_config = await client.get("/api/server-status/7/config", headers=bearer_only)
        no_key_report = await client.post(
            "/api/server-status/7/report",
            headers=bearer_only,
            json={"event_type": "startup"},
        )
        wrong_config = await client.get("/api/server-status/7/config", headers=wrong_key)
        wrong_report = await client.post(
            "/api/server-status/7/report",
            headers=wrong_key,
            json={"event_type": "startup"},
        )
        cross_server_config = await client.get("/api/server-status/8/config", headers=valid_key)
        cross_server_report = await client.post(
            "/api/server-status/8/report",
            headers=valid_key,
            json={"event_type": "startup"},
        )
        config = await client.get("/api/server-status/7/config", headers=valid_key)
        report = await client.post(
            "/api/server-status/7/report",
            headers=valid_key,
            json={"event_type": "startup"},
        )

    for response in (no_key_config, no_key_report, wrong_config, wrong_report):
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == {"detail": "Invalid API key"}
    for response in (cross_server_config, cross_server_report):
        assert response.status_code == status.HTTP_403_FORBIDDEN

    assert config.status_code == status.HTTP_200_OK
    assert config.json() == {
        "server_id": 7,
        "name": "agent-seven",
        "game_port": 27015,
        "default_map": "de_ancient",
        "max_players": 24,
        "game_mode": "competitive",
        "game_type": "0",
    }
    assert report.status_code == status.HTTP_200_OK
    assert report.json() == {
        "success": True,
        "message": "Status report received",
        "server_id": 7,
        "event_type": "startup",
        "current_status": "running",
    }
    serialized = f"{config.text}\n{report.text}"
    for secret_name in (
        "api_key",
        "ssh_password",
        "sudo_password",
        "server_password",
        "rcon_password",
        "steam_account_token",
        "discord_webhook_url",
    ):
        assert secret_name not in serialized
    assert AGENT_API_KEY not in serialized

    assert auth_database.contexts
    assert all(context.closed for context in auth_database.contexts)
    assert write_session.committed is True
    assert len(write_session.added) == 1
    assert len(write_session.statements) == 1


class _Pool:
    def __init__(self):
        self.called = False

    async def get_pool_stats(self):
        self.called = True
        return {
            "total_connections": 2,
            "alive_connections": 2,
            "in_use_connections": 1,
            "idle_connections": 1,
            "idle_timeout": 300,
            "max_lifetime": 3600,
            "max_connections": 50,
            "available_capacity": 48,
        }


@pytest.mark.asyncio
async def test_pool_stats_remains_admin_only_without_request_db_checkout():
    pool = _Pool()
    app = FastAPI()
    app.state.container = SimpleNamespace(ssh_pool=pool)
    db_checkouts = 0

    async def current_principal(x_role: str | None = Header(default=None)):
        return Principal(
            id=1,
            username="operator",
            email="operator@example.com",
            is_admin=x_role == "admin",
        )

    async def request_db():
        nonlocal db_checkouts
        db_checkouts += 1
        yield object()

    app.dependency_overrides[get_current_principal] = current_principal
    app.dependency_overrides[get_db] = request_db
    app.include_router(server_status.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        regular_user = await client.get(
            "/api/server-status/pool/stats",
            headers={"X-Role": "user"},
        )
        admin = await client.get(
            "/api/server-status/pool/stats",
            headers={"X-Role": "admin"},
        )

    assert regular_user.status_code == status.HTTP_403_FORBIDDEN
    assert pool.called is True
    assert admin.status_code == status.HTTP_200_OK
    assert admin.json()["pool_stats"]["total_connections"] == 2
    assert db_checkouts == 0


def test_server_agent_routes_declare_separate_security_policies():
    routes = {route.path: route for route in server_status.router.routes}
    report_dependencies = {
        dependency.call
        for dependency in routes["/api/server-status/{server_id}/report"].dependant.dependencies
    }
    config_dependencies = {
        dependency.call
        for dependency in routes["/api/server-status/{server_id}/config"].dependant.dependencies
    }
    pool_dependencies = {
        dependency.call
        for dependency in routes["/api/server-status/pool/stats"].dependant.dependencies
    }

    assert server_status.verify_server_api_key in report_dependencies
    assert server_status.verify_server_api_key in config_dependencies
    assert get_admin_principal not in report_dependencies | config_dependencies
    assert get_admin_principal in pool_dependencies
    assert get_db not in pool_dependencies
    assert server_status.verify_server_api_key not in pool_dependencies
