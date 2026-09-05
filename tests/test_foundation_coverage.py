"""Deterministic coverage for transport, authentication and app foundations."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api import dependencies
from api import lifecycle as lifecycle_module
from modules import auth
from modules.http_helper import HTTPHelper
from services.ai_events import AIEventHub
from services.captcha_service import CaptchaService


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class _FakeDb:
    def __init__(self, *, get_result=None, execute_result=None):
        self.get_result = get_result
        self.execute_result = execute_result
        self.committed = False
        self.closed = False
        self.expunge_calls = []
        self.is_active = True

    async def get(self, *_args):
        return self.get_result

    async def execute(self, *_args):
        return self.execute_result

    async def commit(self):
        self.committed = True

    async def close(self):
        self.closed = True

    def expunge(self, value):
        self.expunge_calls.append(value)


def _user(user_id=7, *, active=True, admin=False, owner_id=None):
    return SimpleNamespace(
        id=user_id,
        is_active=active,
        is_admin=admin,
        user_id=user_id if owner_id is None else owner_id,
    )


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _StreamResponse:
    def __init__(self, status_code=200, body=b"abc", *, headers=None):
        self.status_code = status_code
        self.body = body
        self.headers = headers or {"Content-Length": str(len(body))}

    async def aiter_bytes(self, *, chunk_size):
        assert chunk_size == 8192
        for offset in range(0, len(self.body), 2):
            yield self.body[offset : offset + 2]

    async def aread(self):
        return self.body


class _StreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return None


class _StreamClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _StreamContext(next(self.responses))


class _Pipeline:
    def __init__(self):
        self.calls = []

    def rpush(self, *args):
        self.calls.append(("rpush", args))

    def ltrim(self, *args):
        self.calls.append(("ltrim", args))

    def expire(self, *args):
        self.calls.append(("expire", args))

    async def execute(self):
        return []


class _RedisClient:
    def __init__(self, values=None):
        self.values = values or []
        self.pipeline_obj = _Pipeline()
        self.deleted = []

    def pipeline(self, **_kwargs):
        return self.pipeline_obj

    async def lrange(self, *_args):
        return self.values

    async def delete(self, key):
        self.deleted.append(key)


@pytest.mark.asyncio
async def test_http_helper_request_success_json_text_and_proxy_headers(monkeypatch):
    helper = HTTPHelper()
    requests = []

    async def request(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, text="plain")

    helper._get_client = AsyncMock(return_value=SimpleNamespace(request=request))
    result = await helper.make_request(
        "GET",
        "https://api.github.com/repos/a/b",
        headers={"X-Test": "yes"},
        proxy=" https://proxy.example/ ",
        github_token=" token ",
        retries=1,
    )
    assert result == (True, {"ok": True}, None)
    assert requests[0]["url"] == "https://api.github.com/repos/a/b"
    assert requests[0]["headers"]["Authorization"] == "Bearer token"

    result = await helper.make_request(
        "GET",
        "https://github.com/a/b/releases/download/v1/a.zip",
        proxy="https://proxy.example/",
        github_token="",
        retries=1,
    )
    assert result == (True, {"text": "plain"}, None)
    assert requests[1]["url"].startswith("https://proxy.example/https://github.com/")


@pytest.mark.asyncio
async def test_http_helper_error_retry_and_wrappers(monkeypatch):
    helper = HTTPHelper()
    responses = [httpx.Response(500, text="bad"), httpx.Response(200, json={"ok": 2})]
    calls = 0

    async def request(**_kwargs):
        nonlocal calls
        calls += 1
        return responses.pop(0)

    helper._get_client = AsyncMock(return_value=SimpleNamespace(request=request))
    monkeypatch.setattr("modules.http_helper.asyncio.sleep", AsyncMock())
    assert await helper.get("https://example.test", retries=2) == (True, {"ok": 2}, None)
    assert calls == 2

    helper.make_request = AsyncMock(return_value=(True, {"posted": True}, None))
    assert await helper.post("https://example.test", json={"x": 1}) == (
        True,
        {"posted": True},
        None,
    )
    helper.make_request.assert_awaited_once()

    helper = HTTPHelper()
    helper._get_client = AsyncMock(
        return_value=SimpleNamespace(request=AsyncMock(return_value=httpx.Response(404, text="no")))
    )
    ok, data, error = await helper.make_request("GET", "https://example.test", retries=3)
    assert (ok, data) == (False, None)
    assert error == "HTTP 404: no"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception, expected",
    [
        (httpx.TimeoutException("slow"), "Request timeout: slow"),
        (
            httpx.RequestError("offline", request=httpx.Request("GET", "https://x")),
            "Request error: offline",
        ),
        (RuntimeError("broken"), "Unexpected error: broken"),
    ],
)
async def test_http_helper_exhausts_each_request_failure(monkeypatch, exception, expected):
    helper = HTTPHelper()
    helper._get_client = AsyncMock(
        return_value=SimpleNamespace(request=AsyncMock(side_effect=exception))
    )
    monkeypatch.setattr("modules.http_helper.asyncio.sleep", AsyncMock())
    ok, data, error = await helper.make_request("GET", "https://example.test", retries=2)
    assert ok is False and data is None
    assert expected in error
    assert "after 2 attempts" in error


@pytest.mark.asyncio
async def test_http_helper_downloads_with_sync_and_async_progress(monkeypatch, tmp_path):
    helper = HTTPHelper()
    sync_progress = []
    client = _StreamClient([_StreamResponse(body=b"abcdef")])
    helper._get_client = AsyncMock(return_value=client)
    target = tmp_path / "nested" / "file.bin"
    assert await helper.download_file(
        "https://example.test/file",
        str(target),
        progress_callback=lambda done, total: sync_progress.append((done, total)),
    ) == (True, None)
    assert target.read_bytes() == b"abcdef"
    assert sync_progress[-1] == (6, 6)

    async_progress = []

    async def progress(done, total):
        async_progress.append((done, total))

    helper._get_client = AsyncMock(return_value=_StreamClient([_StreamResponse(body=b"xy")]))
    assert await helper.download_file(
        "https://example.test/x", str(tmp_path / "x"), progress_callback=progress
    ) == (
        True,
        None,
    )
    assert async_progress == [(2, 2)]

    transfer_events = []
    helper._get_client = AsyncMock(
        return_value=_StreamClient([_StreamResponse(body=b"xyz", headers={"Content-Length": "0"})])
    )
    assert await helper.download_file(
        "https://example.test/unknown-size",
        str(tmp_path / "unknown-size"),
        progress_event_callback=transfer_events.append,
    ) == (True, None)
    assert transfer_events[0]["phase"] == "download"
    assert transfer_events[-1]["bytes_transferred"] == 3
    assert transfer_events[-1]["total_bytes"] is None


@pytest.mark.asyncio
async def test_http_helper_download_error_paths(monkeypatch, tmp_path):
    helper = HTTPHelper()
    monkeypatch.setattr("modules.http_helper.asyncio.sleep", AsyncMock())
    helper._get_client = AsyncMock(return_value=_StreamClient([_StreamResponse(403, b"denied")]))
    assert await helper.download_file("https://x", str(tmp_path / "x")) == (
        False,
        "HTTP 403: denied",
    )

    helper._get_client = AsyncMock(
        return_value=_StreamClient([_StreamResponse(503, b"upstream")] * 3)
    )
    ok, error = await helper.download_file("https://x", str(tmp_path / "y"))
    assert ok is False and "after 3 attempts" in error

    retry_events = []
    helper._get_client = AsyncMock(
        return_value=_StreamClient([_StreamResponse(503, b"upstream"), _StreamResponse(200, b"ok")])
    )
    ok, error = await helper.download_file(
        "https://x",
        str(tmp_path / "retry-ok"),
        progress_event_callback=retry_events.append,
    )
    assert (ok, error) == (True, None)
    assert any(event["retry_count"] == 1 for event in retry_events)

    helper._get_client = AsyncMock(
        return_value=SimpleNamespace(
            stream=lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.TimeoutException("slow"))
        )
    )
    ok, error = await helper.download_file("https://x", str(tmp_path / "z"))
    assert ok is False and "Download timeout: slow" in error


@pytest.mark.asyncio
async def test_http_helper_client_lifecycle(monkeypatch):
    helper = HTTPHelper()
    first = await helper._get_client()
    assert await helper._get_client() is first
    await helper.close()
    assert helper._client is None
    await helper.close()
    monkeypatch.setattr(httpx.AsyncClient, "aclose", AsyncMock())


def test_auth_password_cookie_and_token_helpers(monkeypatch):
    assert auth._bcrypt_password_bytes("😀" * 100)
    monkeypatch.setattr(auth.bcrypt, "checkpw", lambda *_args: True)
    assert auth.verify_password("plain", "hash") is True
    monkeypatch.setattr(auth.bcrypt, "checkpw", lambda *_args: (_ for _ in ()).throw(ValueError()))
    assert auth.verify_password("plain", "hash") is False
    monkeypatch.setattr(auth.bcrypt, "gensalt", lambda **_kwargs: b"salt")
    monkeypatch.setattr(auth.bcrypt, "hashpw", lambda *_args: b"$2b$hash")
    assert auth.get_password_hash("plain") == "$2b$hash"

    monkeypatch.setattr(auth.settings, "SESSION_COOKIE_SUFFIX", " test ")
    assert auth.web_session_cookie_name() == "upkk_access_token_test"
    monkeypatch.setattr(auth.settings, "SESSION_COOKIE_SUFFIX", "")
    assert auth.web_session_cookie_name() == "upkk_access_token"

    class CookieResponse:
        def __init__(self):
            self.set_args = None
            self.delete_args = None

        def set_cookie(self, **kwargs):
            self.set_args = kwargs

        def delete_cookie(self, *args, **kwargs):
            self.delete_args = (args, kwargs)

    response = CookieResponse()
    auth.set_web_session_cookie(
        SimpleNamespace(url=SimpleNamespace(scheme="https")), response, "tok"
    )
    assert response.set_args["key"] == "upkk_access_token"
    assert response.set_args["secure"] is True
    auth.clear_web_session_cookie(response)
    assert response.delete_args == (("upkk_access_token",), {"path": "/", "samesite": "lax"})

    monkeypatch.setattr(
        auth, "get_current_time", lambda: __import__("datetime").datetime(2025, 1, 1)
    )
    token = auth.create_access_token({"sub": "3"}, timedelta(seconds=10))
    assert (
        auth.jwt.decode(
            token,
            auth.settings.JWT_SECRET_KEY,
            algorithms=[auth.settings.JWT_ALGORITHM],
            options={"verify_exp": False},
        )["sub"]
        == "3"
    )


@pytest.mark.asyncio
async def test_auth_user_dependencies_and_flexible_fallback(monkeypatch):
    active = _user()
    inactive = _user(active=False)
    monkeypatch.setattr(
        auth,
        "_decode_user_id",
        lambda token: 7 if token == "good" else (_ for _ in ()).throw(ValueError()),
    )
    assert await auth._get_active_user_for_token("bad", _FakeDb(get_result=active)) is None
    assert await auth._get_active_user_for_token("good", _FakeDb(get_result=inactive)) is None
    assert await auth._get_active_user_for_token("good", _FakeDb(get_result=active)) is active

    with pytest.raises(HTTPException) as exc:
        await auth.get_current_web_user(SimpleNamespace(cookies={}), _FakeDb())
    assert exc.value.status_code == 303
    assert (
        await auth.get_current_web_user(
            SimpleNamespace(cookies={auth.web_session_cookie_name(): "good"}),
            _FakeDb(get_result=active),
        )
        is active
    )
    with pytest.raises(HTTPException):
        await auth.get_current_web_admin(_user(admin=False))
    assert await auth.get_current_web_admin(_user(admin=True))

    db = _FakeDb(execute_result=_Result(active))
    monkeypatch.setattr(auth.jwt, "decode", lambda *_args, **_kwargs: {"sub": "7"})
    assert await auth.get_current_user("token", db) is active
    with pytest.raises(HTTPException):
        await auth.get_current_active_user(_user(active=False))
    assert await auth.get_current_active_user(active) is active
    assert await auth.get_optional_current_user(None, db) is None
    assert (
        await auth.get_optional_current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"), db
        )
        is active
    )
    with pytest.raises(HTTPException):
        await auth.get_current_admin_user(_user(admin=False))

    monkeypatch.setattr(auth.User, "get_by_api_key", AsyncMock(return_value=active))
    assert await auth.get_user_from_api_key(None, db=db) is None
    assert await auth.get_user_from_api_key("key", db=db) is active
    assert await auth.get_current_user_flexible(None, "key", db=db) is active
    with pytest.raises(HTTPException):
        await auth.get_current_user_flexible(None, None, db=db)


@pytest.mark.asyncio
async def test_auth_websocket_origin_session_and_server_access(monkeypatch):
    class Socket:
        def __init__(self, origin, cookies):
            self.headers = {"origin": origin, "host": "panel.example"}
            self.cookies = cookies
            self.closed = None

        async def close(self, **kwargs):
            self.closed = kwargs

    bad_origin = Socket("https://evil.example", {auth.web_session_cookie_name(): "good"})
    assert await auth.authenticate_websocket(bad_origin) == (None, None)
    assert bad_origin.closed["code"] == 4403

    user = _user()
    server = SimpleNamespace(id=5, user_id=7)
    db = _FakeDb(get_result=user)
    db.get_result = user
    monkeypatch.setattr(auth, "_get_active_user_for_token", AsyncMock(return_value=None))

    def maker():
        return _AsyncContext(db)

    monkeypatch.setattr(auth, "async_session_maker", maker)
    invalid = Socket("https://panel.example", {auth.web_session_cookie_name(): "bad"})
    assert await auth.authenticate_websocket(invalid) == (None, None)
    assert invalid.closed["code"] == 4401

    monkeypatch.setattr(auth, "_get_active_user_for_token", AsyncMock(return_value=user))
    db.get_result = None
    denied = Socket("https://panel.example", {auth.web_session_cookie_name(): "good"})
    assert await auth.authenticate_websocket(denied, 5) == (None, None)
    assert denied.closed["code"] == 4404
    db.get_result = server
    assert await auth.authenticate_websocket(
        Socket("https://panel.example", {auth.web_session_cookie_name(): "good"}), 5
    ) == (
        user,
        server,
    )
    assert db.committed


@pytest.mark.asyncio
async def test_api_dependencies_auth_access_and_lock(monkeypatch):
    user = _user()
    db = _FakeDb(get_result=user)
    monkeypatch.setattr(dependencies, "async_session_maker", lambda: _AsyncContext(db))
    monkeypatch.setattr(dependencies, "_get_active_user_for_token", AsyncMock(return_value=user))
    request = SimpleNamespace(cookies={"upkk_access_token": "cookie"})
    assert await dependencies.get_bearer_or_cookie_user(request, None) is user
    assert db.expunge_calls == [user]
    with pytest.raises(HTTPException):
        await dependencies.get_bearer_or_cookie_user(SimpleNamespace(cookies={}), None)

    assert dependencies.get_app_settings() is dependencies.get_settings()
    services = SimpleNamespace(ssh_manager_factory=lambda: "ssh")
    assert (
        dependencies.get_service_container(
            SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=services)))
        )
        is services
    )
    assert dependencies.get_ssh_manager(services) == "ssh"
    monkeypatch.setattr(dependencies.Server, "get_by_id", AsyncMock(return_value=user))
    assert await dependencies.require_server_access(db, 1, _user(admin=True), commit=False) is user
    monkeypatch.setattr(dependencies.Server, "get_by_id", AsyncMock(return_value=None))
    with pytest.raises(HTTPException):
        await dependencies.require_server_access(db, 1, _user(admin=True))
    monkeypatch.setattr(dependencies.Server, "get_by_id_and_user", AsyncMock(return_value=user))
    assert await dependencies.require_server_access(db, 1, _user(), commit=True) is user

    lock = AsyncMock()
    monkeypatch.setattr(
        dependencies.maintenance_lock_service, "get", lambda *_args, **_kwargs: _AsyncContext(lock)
    )
    db.get_result = SimpleNamespace(id=1, user_id=user.id)
    request = SimpleNamespace(method="DELETE", url=SimpleNamespace(path="/servers/1"))
    yielded = dependencies.locked_server_operation(request, 1, db, user)
    assert await yielded.__anext__() is db.get_result
    with pytest.raises(StopAsyncIteration):
        await yielded.__anext__()


@pytest.mark.asyncio
async def test_captcha_and_ai_event_hub_paths(monkeypatch):
    captcha = CaptchaService()
    redis = SimpleNamespace(
        client=SimpleNamespace(set=AsyncMock(), get=AsyncMock(), delete=AsyncMock()),
        prefixed_key=lambda key: f"p:{key}",
    )
    monkeypatch.setattr("services.captcha_service.redis_manager", redis)
    monkeypatch.setattr(captcha, "_generate_code", lambda: "ABCD")
    monkeypatch.setattr(
        "services.captcha_service.to_thread.run_sync", AsyncMock(return_value=b"png")
    )
    token, image = await captcha.generate_captcha()
    assert token and image == b"png"
    redis.client.get.return_value = b"abcd"
    assert await captcha.validate_captcha(token, "ABCD") is True
    assert await captcha.validate_captcha("", "ABCD") is False
    redis.client.get.return_value = None
    assert await captcha.validate_captcha(token, "ABCD") is False
    monkeypatch.setattr(captcha, "generate_captcha", AsyncMock(return_value=("new", b"img")))
    assert await captcha.refresh_captcha(token) == ("new", b"img")

    class Client:
        def __init__(self, send_json):
            self.send_json = send_json

    client = Client(AsyncMock())
    failed_client = Client(AsyncMock(side_effect=RuntimeError("gone")))
    hub = AIEventHub()
    queue = await hub.subscribe_queue("run")
    await hub.subscribe("run", client)
    await hub.subscribe("run", failed_client)
    redis_client = _RedisClient()
    monkeypatch.setattr(
        "services.ai_events.redis_manager",
        SimpleNamespace(prefixed_key=lambda x: x, client=redis_client),
    )
    event = await hub.emit("run", "token", {"text": "hi"})
    assert event["payload"] == {"text": "hi"}
    assert queue.get_nowait() == event
    client.send_json.assert_awaited_once()
    assert failed_client not in hub._clients["run"]
    assert await hub.replay("run", int(event["sequence"])) == []
    redis_client.values = [json.dumps(event), b"not json", json.dumps({"sequence": "0"})]
    assert await hub.replay("run") == [event]
    await hub.unsubscribe_queue("run", queue)
    await hub.unsubscribe_queue("missing", queue)


@pytest.mark.asyncio
async def test_lifecycle_success_registers_and_stops_all_services(monkeypatch):
    calls = []

    def service(name):
        return SimpleNamespace(
            start=AsyncMock(side_effect=lambda: calls.append(f"start:{name}")),
            stop=AsyncMock(side_effect=lambda: calls.append(f"stop:{name}")),
        )

    service_names = (
        "a2s_cache_service",
        "ai_retention_service",
        "audit_retention_service",
        "discord_bot_manager",
        "steam_inf_service",
        "auto_update_service",
        "plugin_auto_update_service",
        "scheduled_task_service",
        "ssh_health_monitor",
    )
    for name in service_names:
        module = __import__(f"services.{name}", fromlist=[name])
        monkeypatch.setattr(module, name, service(name))

    class Pool:
        start_cleanup = AsyncMock()
        stop_cleanup = AsyncMock()
        close_all = AsyncMock()

    resources = SimpleNamespace(
        database_engine=SimpleNamespace(dispose=AsyncMock()),
        http=SimpleNamespace(close=AsyncMock()),
        ai_http=SimpleNamespace(close=AsyncMock()),
        redis=SimpleNamespace(close=AsyncMock(), delete_by_pattern=AsyncMock(return_value=2)),
        ssh_pool=Pool(),
        session_factory=lambda: _AsyncContext(None),
    )
    container = SimpleNamespace(resources=resources)
    lifecycle = lifecycle_module.ApplicationLifecycle(container)
    monkeypatch.setattr(lifecycle_module, "migrate_db", AsyncMock())
    monkeypatch.setattr(lifecycle_module, "init_db", AsyncMock())
    monkeypatch.setattr(lifecycle_module, "async_session_maker", lambda: _AsyncContext(None))
    monkeypatch.setattr(
        lifecycle_module.Server, "get_all_with_panel_monitoring", AsyncMock(return_value=[])
    )
    catalog = __import__("services.plugin_catalog", fromlist=["ensure_default_plugin_catalog"])
    monkeypatch.setattr(catalog, "ensure_default_plugin_catalog", AsyncMock(return_value=None))
    security = __import__("services.ai_security", fromlist=["initialize_credential_encryption"])
    monkeypatch.setattr(security, "initialize_credential_encryption", lambda: "test")

    await lifecycle.start()
    assert lifecycle.started is True
    assert "SSH connection pool" in lifecycle.cleanup_names
    await lifecycle.start()
    await lifecycle.stop()
    assert lifecycle.started is False
    assert calls[:3] == [
        "start:a2s_cache_service",
        "start:ai_retention_service",
        "start:audit_retention_service",
    ]
