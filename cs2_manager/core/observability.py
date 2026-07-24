"""Request correlation primitives shared by logs and HTTP responses."""

from __future__ import annotations

import contextvars
import logging
import re
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .metrics import MetricsRegistry

logger = logging.getLogger("cs2_manager.http")

REQUEST_ID_HEADER = b"x-request-id"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
request_id_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id",
    default=None,
)


def current_request_id() -> str | None:
    """Return the current request ID for logging adapters and services."""
    return request_id_context.get()


def _request_id_from_scope(scope: Scope) -> str:
    for key, value in scope.get("headers", ()):
        if key.lower() != REQUEST_ID_HEADER:
            continue
        candidate = value.decode("ascii", errors="ignore")
        if REQUEST_ID_PATTERN.fullmatch(candidate):
            return candidate
    return uuid.uuid4().hex


class RequestIDMiddleware:
    """Propagate a safe request ID and bind it to the current async context."""

    def __init__(
        self,
        app: ASGIApp,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        self.app = app
        self.metrics_registry = metrics_registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from_scope(scope)
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id
        token = request_id_context.set(request_id)
        started_at = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != REQUEST_ID_HEADER
                ]
                headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            elapsed_seconds = time.perf_counter() - started_at
            route_object = scope.get("route")
            route = getattr(route_object, "path", None) or "unmatched"
            if self.metrics_registry is not None:
                self.metrics_registry.observe_http(
                    method=scope.get("method", ""),
                    route=route,
                    status_code=status_code,
                    duration_seconds=elapsed_seconds,
                )
            logger.info(
                "HTTP request completed",
                extra={
                    "http_method": scope.get("method", ""),
                    "http_path": scope.get("path", ""),
                    "http_route": route,
                    "http_status_code": status_code,
                    "duration_ms": round(elapsed_seconds * 1000, 3),
                },
            )
            request_id_context.reset(token)
