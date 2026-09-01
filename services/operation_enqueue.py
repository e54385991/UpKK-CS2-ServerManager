"""Bindable hub enqueue entry points for service-layer callers.

HTTP routes own the runners in ``api.routes.v1.operation_runner``. Services must
not import that transport module, so the runner binds these facades at import
time and cron / Discord / AI / scheduled tasks enqueue through here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

EnqueuePluginAutoUpdate = Callable[..., Awaitable[dict[str, Any]]]
EnqueueServerOperation = Callable[..., Awaitable[dict[str, Any]]]

_plugin_auto_update: EnqueuePluginAutoUpdate | None = None
_server_operation: EnqueueServerOperation | None = None


def bind_hub_enqueuers(
    *,
    plugin_auto_update: EnqueuePluginAutoUpdate,
    server_operation: EnqueueServerOperation,
) -> None:
    global _plugin_auto_update, _server_operation
    _plugin_auto_update = plugin_auto_update
    _server_operation = server_operation


def _bound(handler: Callable[..., Awaitable[dict[str, Any]]] | None, name: str):
    if handler is None:
        raise RuntimeError(f"{name} is not bound; load the operation runner first")
    return handler


async def enqueue_plugin_auto_update(**kwargs: Any) -> dict[str, Any]:
    handler = _bound(_plugin_auto_update, "enqueue_plugin_auto_update")
    return await handler(**kwargs)


async def enqueue_server_operation(**kwargs: Any) -> dict[str, Any]:
    handler = _bound(_server_operation, "enqueue_server_operation")
    return await handler(**kwargs)
