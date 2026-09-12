"""ASGI middleware that records request duration without buffering bodies.

Server-Timing is set when response headers start so streaming/SSE still work.
The diagnostics snapshot and ``/health`` are timed in the header but not stored,
so liveness probes do not pollute percentiles.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from time import perf_counter

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from modules.observability import (
    begin_request,
    bind_request,
    end_request,
    generation,
    is_enabled,
    record_cancellation,
    record_error,
    record_http,
    record_stream_error,
    record_unhandled,
    reset_request,
)
from services.panel_metrics import metrics_store, route_label, should_record_path


@dataclass
class _HttpTrace:
    started: float
    path: str
    method: str
    request_id: str
    generation: int
    template: str | None
    recorded: bool = False
    first_byte_ms: float | None = None
    response_status: int = 0
    sse: bool = False
    disconnected: bool = False
    counted: bool = False


class RequestMetricsMiddleware:
    """Record matched HTTP timings and expose them as Server-Timing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        request_id = _request_id(scope)
        gen = generation()
        trace = _HttpTrace(
            started=perf_counter(),
            path=path,
            method=str(scope.get("method") or "GET"),
            request_id=request_id,
            generation=gen,
            template=_route_template(scope),
            counted=is_enabled(),
        )
        token = bind_request(request_id=request_id, generation=gen)
        if trace.counted:
            begin_request()
        try:
            await self.app(
                scope, _receive_with_disconnect(trace, receive), _send_with_timing(trace, send)
            )
        except asyncio.CancelledError:
            _observe_cancellation(trace)
            raise
        except Exception as exc:
            _observe_exception(trace, exc)
            raise
        else:
            _observe_success(trace)
        finally:
            if trace.counted:
                end_request()
            reset_request(token)


def _receive_with_disconnect(trace: _HttpTrace, receive: Receive) -> Receive:
    async def receive_with_disconnect() -> Message:
        message = await receive()
        if message["type"] == "http.disconnect":
            trace.disconnected = True
        return message

    return receive_with_disconnect


def _send_with_timing(trace: _HttpTrace, send: Send) -> Send:
    async def send_with_timing(message: Message) -> None:
        if message["type"] == "http.response.start" and not trace.recorded:
            trace.recorded = True
            trace.first_byte_ms = (perf_counter() - trace.started) * 1000
            headers = MutableHeaders(raw=list(message["headers"]))
            headers.append("server-timing", f"app;dur={trace.first_byte_ms:.1f}")
            headers["x-request-id"] = trace.request_id
            message["headers"] = headers.raw
            trace.response_status = int(message["status"])
            trace.sse = _is_sse(headers)
            if should_record_path(trace.path) and not trace.sse:
                _store_request(
                    method=trace.method,
                    path=trace.path,
                    template=trace.template,
                    status=trace.response_status,
                    duration_ms=trace.first_byte_ms or 0.0,
                    generation=trace.generation,
                    sse=False,
                )
        await send(message)

    return send_with_timing


def _observe_cancellation(trace: _HttpTrace) -> None:
    if is_enabled() and should_record_path(trace.path):
        record_cancellation(generation=trace.generation)


def _observe_exception(trace: _HttpTrace, exc: Exception) -> None:
    if not is_enabled() or not should_record_path(trace.path):
        return
    if trace.recorded:
        record_stream_error(generation=trace.generation)
        return
    record_unhandled(generation=trace.generation)
    record_error(
        source="request",
        summary=str(exc),
        error_code="unhandled",
        exception=exc,
        route=route_label(trace.path, trace.template),
        request_id=trace.request_id,
        generation=trace.generation,
    )


def _observe_success(trace: _HttpTrace) -> None:
    if trace.disconnected:
        _observe_cancellation(trace)
    if trace.recorded and trace.sse and should_record_path(trace.path):
        _store_request(
            method=trace.method,
            path=trace.path,
            template=trace.template,
            status=trace.response_status,
            duration_ms=(perf_counter() - trace.started) * 1000,
            generation=trace.generation,
            sse=True,
            first_byte_ms=trace.first_byte_ms,
        )


def _store_request(
    *,
    method: str,
    path: str,
    template: str | None,
    status: int,
    duration_ms: float,
    generation: int,
    sse: bool,
    first_byte_ms: float | None = None,
) -> None:
    route = route_label(path, template)
    if not is_enabled():
        return
    record_http(
        method=method,
        route=route,
        status=status,
        duration_ms=duration_ms,
        generation=generation,
        sse=sse,
        first_byte_ms=first_byte_ms,
    )
    metrics_store.record_request(
        method=method,
        route=route,
        status=status,
        duration_ms=duration_ms,
    )


def _request_id(scope: Scope) -> str:
    headers = dict(scope.get("headers") or [])
    incoming = headers.get(b"x-request-id")
    if incoming:
        value = incoming.decode("latin-1").strip()
        if value:
            return value[:128]
    return uuid.uuid4().hex


def _is_sse(headers: MutableHeaders) -> bool:
    content_type = headers.get("content-type", "")
    return "text/event-stream" in content_type.lower()


def _route_template(scope: Scope) -> str | None:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else None
