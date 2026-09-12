"""ASGI request-metrics middleware records timings without buffering bodies."""

from __future__ import annotations

import asyncio

import pytest

from api.request_metrics import RequestMetricsMiddleware
from modules.observability import (
    clear_all,
    drain_errors,
    reset_for_tests,
    set_enabled,
    snapshot_current_bucket,
)
from services.panel_metrics import metrics_store


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    clear_all()
    metrics_store.clear()
    yield
    reset_for_tests()
    clear_all()
    metrics_store.clear()


async def _invoke(app, *, path="/api/v1/servers", headers=None, method="GET"):
    middleware = RequestMetricsMiddleware(app)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "path": path,
        "method": method,
        "headers": headers or [],
    }
    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_middleware_ignores_non_http_scopes():
    called = {"ok": False}

    async def app(scope, receive, send):
        called["ok"] = True

    middleware = RequestMetricsMiddleware(app)
    await middleware({"type": "websocket"}, lambda: None, lambda _m: None)
    assert called["ok"] is True


@pytest.mark.asyncio
async def test_middleware_records_http_and_incoming_request_id():
    set_enabled(True)

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    sent = await _invoke(app, headers=[(b"x-request-id", b"req-fixed")])
    start = next(item for item in sent if item["type"] == "http.response.start")
    headers = {key: value for key, value in start["headers"]}
    assert headers[b"x-request-id"] == b"req-fixed"
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["requests"] == 1
    assert snapshot["http_count"] == 1


@pytest.mark.asyncio
async def test_middleware_records_sse_after_complete_response():
    set_enabled(True)

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": b"data: x\n\n", "more_body": False})

    await _invoke(app, path="/ops-stream/1")
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["sse_count"] == 1


@pytest.mark.asyncio
async def test_middleware_records_unhandled_before_headers():
    set_enabled(True)

    async def app(scope, receive, send):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await _invoke(app)
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["unhandled"] == 1
    assert any(item["error_code"] == "unhandled" for item in drain_errors())


@pytest.mark.asyncio
async def test_middleware_records_stream_error_after_headers():
    set_enabled(True)

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        raise RuntimeError("stream broke")

    with pytest.raises(RuntimeError):
        await _invoke(app)
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["stream_errors"] == 1


@pytest.mark.asyncio
async def test_middleware_records_cancellation_and_disconnect():
    set_enabled(True)

    async def app(scope, receive, send):
        await receive()
        raise asyncio.CancelledError

    middleware = RequestMetricsMiddleware(app)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    scope = {"type": "http", "path": "/api/v1/servers", "method": "GET", "headers": []}
    with pytest.raises(asyncio.CancelledError):
        await middleware(scope, receive, send)
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["cancellations"] >= 1


@pytest.mark.asyncio
async def test_middleware_skips_diagnostics_path():
    set_enabled(True)

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    await _invoke(app, path="/api/v1/diagnostics")
    snapshot = snapshot_current_bucket()
    assert snapshot is not None
    assert snapshot["requests"] == 0
