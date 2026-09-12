"""ASGI middleware that records request duration without buffering bodies.

Server-Timing is set when response headers start so streaming/SSE still work.
The diagnostics snapshot and ``/health`` are timed in the header but not stored,
so liveness probes do not pollute percentiles.
"""

from __future__ import annotations

from time import perf_counter

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from services.panel_metrics import metrics_store, route_label, should_record_path


class RequestMetricsMiddleware:
    """Record matched HTTP timings and expose them as Server-Timing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        recorded = False

        async def send_with_timing(message: Message) -> None:
            nonlocal recorded
            if message["type"] == "http.response.start" and not recorded:
                recorded = True
                duration_ms = (perf_counter() - started) * 1000
                headers = MutableHeaders(raw=list(message["headers"]))
                headers.append("server-timing", f"app;dur={duration_ms:.1f}")
                message["headers"] = headers.raw
                path = scope.get("path") or ""
                if should_record_path(path):
                    template = _route_template(scope)
                    metrics_store.record_request(
                        method=str(scope.get("method") or "GET"),
                        route=route_label(path, template),
                        status=int(message["status"]),
                        duration_ms=duration_ms,
                    )
            await send(message)

        await self.app(scope, receive, send_with_timing)


def _route_template(scope: Scope) -> str | None:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else None
