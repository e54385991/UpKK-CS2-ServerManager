from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from api.application import create_app
from api.dependencies import get_admin_principal
from api.routes import gmail_oauth, public, server_status
from api.routes.file_manager import files as file_routes
from api.routes.servers import crud, monitoring
from cs2_manager.core import Principal
from modules.auth import (
    create_access_token,
    get_current_principal,
)
from modules.config import settings as default_settings
from modules.models import AuthType, Server, ServerStatus
from modules.schemas import (
    ServerCreatedResponse,
    ServerDetail,
    ServerResponse,
    ServerSummary,
    ServerUpdate,
)
from services.a2s_cache_service import a2s_cache_service

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _server(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": 1,
        "user_id": 7,
        "name": "test",
        "host": "panel.example",
        "ssh_user": "cs2",
        "auth_type": AuthType.PASSWORD,
        "status": ServerStatus.STOPPED,
        "server_password": "server-secret",
        "rcon_password": "rcon-secret",
        "steam_account_token": "GSLTSECRET",
        "api_key": "agent-secret",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Server(**values)


def _dependency_calls(router, path):
    route = next(route for route in router.routes if route.path == path)
    return {dependency.call for dependency in route.dependant.dependencies}


def test_server_responses_replace_secrets_with_configuration_flags():
    server = _server()

    payload = ServerResponse.model_validate(server).model_dump()

    assert {
        "server_password",
        "rcon_password",
        "steam_account_token",
        "api_key",
    }.isdisjoint(payload)
    assert payload["server_password_configured"] is True
    assert payload["rcon_password_configured"] is True
    assert payload["steam_account_token_configured"] is True
    assert payload["api_key_configured"] is True

    created = ServerCreatedResponse(**payload, api_key=server.api_key)
    assert created.api_key == "agent-secret"


def test_server_secret_updates_distinguish_omitted_replaced_and_cleared():
    assert "server_password" not in ServerUpdate().model_dump(exclude_unset=True)
    assert ServerUpdate(server_password="replacement").model_dump(exclude_unset=True) == {
        "server_password": "replacement"
    }
    assert ServerUpdate(server_password=None).model_dump(exclude_unset=True) == {
        "server_password": None
    }


def test_server_summary_and_detail_are_explicit_secret_free_contracts():
    legacy_fields = set(ServerResponse.model_fields)

    # The compatibility release keeps the existing JSON shape while giving
    # list and detail responses independent public evolution points.
    assert set(ServerSummary.model_fields) == legacy_fields
    assert set(ServerDetail.model_fields) == legacy_fields
    assert {
        "server_password",
        "rcon_password",
        "steam_account_token",
        "api_key",
    }.isdisjoint(legacy_fields)


def test_ssh_host_key_openapi_declares_expected_error_envelopes():
    paths = create_app(lifespan=None).openapi()["paths"]
    expected = {
        "/servers/ssh-host-key/scan": {"400", "504"},
        "/servers/{server_id}/ssh-host-key/scan": {"400", "404", "504"},
        "/servers/{server_id}/ssh-host-key/confirm": {"400", "404", "409", "504"},
    }

    for path, status_codes in expected.items():
        responses = paths[path]["post"]["responses"]
        for status_code in status_codes:
            schema = responses[status_code]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorResponse"}


def test_server_templates_use_json_escaping_and_never_reload_secrets():
    scripts = (PROJECT_ROOT / "templates" / "server_detail_includes" / "scripts.html").read_text(
        encoding="utf-8"
    )
    configuration = (
        PROJECT_ROOT / "templates" / "server_detail_includes" / "configuration_tab.html"
    ).read_text(encoding="utf-8")
    editor = (PROJECT_ROOT / "templates" / "file_editor_popup.html").read_text(encoding="utf-8")

    assert "server_json | tojson" in scripts
    assert "server_json | safe" not in scripts
    assert "server.game_directory | tojson" in scripts
    assert "this.server.server_password" not in scripts
    assert "this.server.rcon_password" not in scripts
    assert "this.server.steam_account_token" not in scripts
    assert "server.server_password_configured" in configuration
    assert "server.rcon_password_configured" in configuration
    assert "server.steam_account_token_configured" in configuration
    assert "file_path | tojson" in editor
    assert "file_name | tojson" in editor
    assert "| safe" not in editor


def test_api_driven_template_content_uses_dom_safe_sinks():
    plugin_market = (PROJECT_ROOT / "templates" / "plugin_market.html").read_text(encoding="utf-8")
    login = (PROJECT_ROOT / "templates" / "login.html").read_text(encoding="utf-8")
    system_settings = (PROJECT_ROOT / "templates" / "system_settings.html").read_text(
        encoding="utf-8"
    )
    server_scripts = (
        PROJECT_ROOT / "templates" / "server_detail_includes" / "scripts.html"
    ).read_text(encoding="utf-8")

    assert "container.innerHTML = plugins.map" not in plugin_market
    assert 'onclick="showInstallModal(${plugin.id}' not in plugin_market
    assert 'onclick="showUninstallModal(${plugin.id}' not in plugin_market
    assert "() => showInstallModal(plugin.id, pluginTitle)" in plugin_market
    assert "() => showUninstallModal(plugin.id, pluginTitle)" in plugin_market
    assert "safeHttpUrl(plugin.github_url)" in plugin_market
    assert "safeHttpUrl(plugin.icon_url)" in plugin_market
    assert 'label.innerHTML = `<i class="bi bi-folder"></i> ${dir}`' not in plugin_market
    assert 'fileListDiv.innerHTML = `<p class="text-danger">${data.error' not in plugin_market
    assert 'fileListDiv.innerHTML = `<p class="text-danger">${error.message}' not in plugin_market
    assert "fileListDiv.innerHTML = html" not in plugin_market

    assert "testEmailResult.innerHTML" not in system_settings
    assert "renderTestEmailResult(" in system_settings

    assert "fileListDiv.innerHTML" not in server_scripts
    assert "renderGitHubUninstallMessage(" in server_scripts
    assert "checkbox.addEventListener('change', updateGitHubUninstallCount)" in server_scripts

    assert 'value="${idToken' not in login
    assert "getElementById('google-id-token').value = idToken || '';" in login


def test_a2s_diagnostics_and_pool_stats_require_admin():
    assert get_admin_principal in _dependency_calls(public.router, "/a2s-cache-test")
    assert monitoring.get_admin_principal in _dependency_calls(
        monitoring.router, "/servers/a2s-cache-test"
    )
    assert get_admin_principal in _dependency_calls(
        server_status.router, "/api/server-status/pool/stats"
    )
    assert get_current_principal in _dependency_calls(public.router, "/a2s-cache")
    assert get_current_principal in _dependency_calls(monitoring.router, "/servers/a2s-cache")


class _ServerIdResult:
    def __init__(self, server_ids):
        self.server_ids = server_ids

    def scalars(self):
        return self

    def all(self):
        return self.server_ids


class _A2SDatabase:
    def __init__(self, server_ids):
        self.server_ids = server_ids
        self.statement = None
        self.committed = False
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.closed = True

    async def execute(self, statement):
        self.statement = statement
        return _ServerIdResult(self.server_ids)

    async def commit(self):
        self.committed = True


def _request_for_database(db):
    app = SimpleNamespace(
        state=SimpleNamespace(
            container=SimpleNamespace(
                database=SimpleNamespace(session_factory=lambda: db),
                services={"a2s_cache": a2s_cache_service},
            )
        )
    )
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_a2s_cache_filters_regular_users_and_batches_cache_reads(monkeypatch):
    db = _A2SDatabase([11, 12])

    async def get_many_after_database_commit(server_ids):
        assert db.committed is True
        return {11: {"success": True}, 12: None}

    get_many = AsyncMock(side_effect=get_many_after_database_commit)
    monkeypatch.setattr(a2s_cache_service, "get_cached_info_many", get_many)
    monkeypatch.setattr(a2s_cache_service, "get_latest_steam_version", AsyncMock(return_value=None))

    response = await public.get_user_servers_a2s_cache(
        request=_request_for_database(db),
        uow=SimpleNamespace(  # type: ignore[arg-type]
            session=db,
            commit=db.commit,
        ),
        current_user=Principal(
            id=7,
            username="owner",
            email="owner@example.com",
            is_admin=False,
        ),
    )

    assert "servers.user_id" in str(db.statement)
    assert db.committed is True
    get_many.assert_awaited_once_with([11, 12])
    assert response["servers"] == {"11": {"success": True}}


class _PrincipalDatabase:
    def __init__(self, user):
        self.user = user
        self.closed = False
        self.requested_user_id = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.closed = True

    async def get(self, _model, user_id):
        self.requested_user_id = user_id
        return self.user


@pytest.mark.asyncio
async def test_principal_authentication_closes_its_database_session_before_return():
    user = SimpleNamespace(
        id=7,
        username="owner",
        email="owner@example.com",
        is_admin=False,
        is_active=True,
    )
    db = _PrincipalDatabase(user)
    request = _request_for_database(db)

    principal = await get_current_principal(
        request=request,
        token=create_access_token({"sub": str(user.id)}),
    )

    assert principal == Principal.model_validate(user)
    assert db.requested_user_id == user.id
    assert db.closed is True


@pytest.mark.asyncio
async def test_a2s_cache_admin_query_is_not_owner_filtered(monkeypatch):
    db = _A2SDatabase([11, 12])
    get_many = AsyncMock(return_value={})
    monkeypatch.setattr(a2s_cache_service, "get_cached_info_many", get_many)

    await monitoring.get_all_servers_a2s_cache(
        request=_request_for_database(db),
        uow=SimpleNamespace(session=db, commit=db.commit),  # type: ignore[arg-type]
        current_user=Principal(
            id=1,
            username="admin",
            email="admin@example.com",
            is_admin=True,
        ),
    )

    assert "servers.user_id" not in str(db.statement)
    get_many.assert_awaited_once_with([11, 12])


class _OneTimeRedis:
    def __init__(self, fail=False):
        self.values = {}
        self.fail = fail
        self.set_options = None

    async def set(self, key, value, **options):
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.set_options = options
        if options.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, _script, _key_count, key):
        if self.fail:
            raise ConnectionError("redis unavailable")
        return self.values.pop(key, None)


def _oauth_request(path: str, client=None) -> Request:
    redis = SimpleNamespace(client=client or _OneTimeRedis())
    app = SimpleNamespace(
        state=SimpleNamespace(
            container=SimpleNamespace(redis=redis),
            settings=default_settings,
        )
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "app": app,
        }
    )


@pytest.mark.asyncio
async def test_gmail_oauth_state_is_short_lived_and_consumed_once():
    client = _OneTimeRedis()
    redis = SimpleNamespace(client=client)
    payload = {
        "admin_user_id": 42,
        "code_verifier": "verifier",
        "context_fingerprint": "fingerprint",
    }

    await gmail_oauth._store_oauth_state(redis, "state", payload)

    assert client.set_options == {
        "ex": gmail_oauth.GMAIL_OAUTH_STATE_TTL_SECONDS,
        "nx": True,
    }
    assert await gmail_oauth._consume_oauth_state(redis, "state") == payload
    assert await gmail_oauth._consume_oauth_state(redis, "state") is None


@pytest.mark.asyncio
async def test_gmail_oauth_state_storage_fails_closed():
    redis = SimpleNamespace(client=_OneTimeRedis(fail=True))

    with pytest.raises(gmail_oauth.OAuthStateStoreUnavailable):
        await gmail_oauth._store_oauth_state(redis, "state", {})
    with pytest.raises(gmail_oauth.OAuthStateStoreUnavailable):
        await gmail_oauth._consume_oauth_state(redis, "state")


class _OAuthDatabase:
    def __init__(self, user=None):
        self.user = user
        self.commits = 0
        self.rollbacks = 0

    async def get(self, _model, _identifier):
        return self.user

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _PKCEFlow:
    code_verifier = "pkce-verifier"

    @classmethod
    def from_client_config(cls, _credentials, **_kwargs):
        return cls()

    def authorization_url(self, **_kwargs):
        return "https://accounts.example/authorize?state=oauth-state", "oauth-state"


@pytest.mark.asyncio
async def test_gmail_authorize_binds_pkce_and_admin_to_redis_state(monkeypatch):
    settings_row = SimpleNamespace(
        gmail_credentials_json='{"web":{"client_id":"client"}}',
        gmail_token_json=None,
    )

    async def get_settings(_cls, _db):
        return settings_row

    monkeypatch.setattr(
        gmail_oauth.SystemSettings,
        "get_or_create_settings",
        classmethod(get_settings),
    )
    monkeypatch.setattr("google_auth_oauthlib.flow.Flow", _PKCEFlow)
    store_state = AsyncMock()
    monkeypatch.setattr(gmail_oauth, "_store_oauth_state", store_state)
    response = Response()

    result = await gmail_oauth.gmail_oauth_authorize(
        _oauth_request("/authorize"),
        response=response,
        db=_OAuthDatabase(),
        current_user=SimpleNamespace(id=42, is_admin=True),
    )

    assert result["state"] == "oauth-state"
    assert response.headers["cache-control"] == "no-store"
    stored_payload = store_state.await_args.args[2]
    assert stored_payload["admin_user_id"] == 42
    assert stored_payload["code_verifier"] == "pkce-verifier"
    assert stored_payload["context_fingerprint"] == (
        gmail_oauth._authorization_context_fingerprint(
            settings_row.gmail_credentials_json,
            settings_row.gmail_token_json,
        )
    )


@pytest.mark.asyncio
async def test_gmail_callback_is_bound_to_the_initiating_admin(monkeypatch):
    transaction = {
        "admin_user_id": 42,
        "code_verifier": "verifier",
        "context_fingerprint": "fingerprint",
    }
    monkeypatch.setattr(gmail_oauth, "_consume_oauth_state", AsyncMock(return_value=transaction))
    db = _OAuthDatabase(user=SimpleNamespace(id=42, is_active=True, is_admin=False))

    response = await gmail_oauth.gmail_oauth_callback(
        _oauth_request("/callback"),
        code="code",
        state="state",
        db=db,
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith("gmail_auth=error")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_gmail_callback_returns_503_when_state_cannot_be_verified(monkeypatch):
    monkeypatch.setattr(
        gmail_oauth,
        "_consume_oauth_state",
        AsyncMock(side_effect=gmail_oauth.OAuthStateStoreUnavailable("down")),
    )

    with pytest.raises(HTTPException) as caught:
        await gmail_oauth.gmail_oauth_callback(
            _oauth_request("/callback"),
            code="code",
            state="state",
            db=_OAuthDatabase(),
        )

    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_file_content_api_rejects_remote_symlink_escape(monkeypatch):
    server = _server(game_directory="/srv/cs2")
    monkeypatch.setattr(file_routes, "get_server_for_user", AsyncMock(return_value=server))

    class RejectingSSHManager:
        disconnected = False
        read_called = False

        async def validate_path_within_base(self, *_args, **_kwargs):
            return False, "Path resolves outside the server directory"

        async def read_file(self, *_args, **_kwargs):
            self.read_called = True
            return True, "secret", ""

        async def disconnect(self):
            self.disconnected = True

    manager = RejectingSSHManager()
    monkeypatch.setattr(file_routes, "SSHManager", lambda: manager)

    with pytest.raises(HTTPException) as caught:
        await file_routes.get_file_content(
            server_id=server.id,
            path="/srv/cs2/link/secret",
            db=object(),
            current_user=SimpleNamespace(id=server.user_id, is_admin=False),
        )

    assert caught.value.status_code == 403
    assert manager.read_called is False
    assert manager.disconnected is True


class _UpdateDatabase:
    async def commit(self):
        return None

    async def refresh(self, _value):
        return None


@pytest.mark.asyncio
async def test_ssh_credential_revision_changes_only_when_credentials_change(monkeypatch):
    server = _server(ssh_password="old", credential_revision=3)
    monkeypatch.setattr(crud, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(crud.redis_manager, "clear_server_cache", AsyncMock(return_value=True))

    await crud.update_server(
        server_id=server.id,
        server_data=ServerUpdate(ssh_password="new"),
        ssh_manager=SimpleNamespace(),
        db=_UpdateDatabase(),
        current_user=SimpleNamespace(id=server.user_id, is_admin=False),
    )
    assert server.credential_revision == 4

    await crud.update_server(
        server_id=server.id,
        server_data=ServerUpdate(ssh_password="new"),
        ssh_manager=SimpleNamespace(),
        db=_UpdateDatabase(),
        current_user=SimpleNamespace(id=server.user_id, is_admin=False),
    )
    assert server.credential_revision == 4


@pytest.mark.asyncio
async def test_enabling_monitoring_uses_the_application_ssh_manager(monkeypatch):
    server = _server(enable_panel_monitoring=False)
    manager = SimpleNamespace(name="application-manager")
    start_monitoring = Mock()
    monkeypatch.setattr(crud, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(crud.redis_manager, "clear_server_cache", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "services.server_monitor.server_monitor.start_monitoring",
        start_monitoring,
    )

    await crud.update_server(
        server_id=server.id,
        server_data=ServerUpdate(enable_panel_monitoring=True),
        ssh_manager=manager,
        db=_UpdateDatabase(),
        current_user=SimpleNamespace(id=server.user_id, is_admin=False),
    )

    start_monitoring.assert_called_once_with(server.id, manager)
