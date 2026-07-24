from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from api.application import create_app
from cs2_manager.core.metrics import MetricsRegistry
from cs2_manager.core.observability import request_id_context
from cs2_manager.runtime import TaskSupervisor
from modules.logging_config import JSONFormatter, RequestContextFilter, setup_logging


class _Pool:
    def size(self) -> int:
        return 5

    def checkedin(self) -> int:
        return 3

    def checkedout(self) -> int:
        return 2

    def overflow(self) -> int:
        return 0


class _Database:
    session_factory = None
    engine = SimpleNamespace(pool=_Pool())

    async def ping(self) -> bool:
        return True


class _RedisPool:
    _in_use_connections = {object()}
    _available_connections = [object(), object()]
    max_connections = 10


class _Redis:
    client = SimpleNamespace(connection_pool=_RedisPool())

    async def ping(self) -> bool:
        return True


class _Closeable:
    async def close(self) -> None:
        pass


class _SSHPool:
    async def get_pool_stats(self) -> dict[str, int]:
        return {
            "total_connections": 4,
            "alive_connections": 4,
            "in_use_connections": 1,
            "idle_connections": 3,
            "max_connections": 50,
            "available_capacity": 46,
        }


def _metrics_app(token: str | None):
    settings = SimpleNamespace(METRICS_BEARER_TOKEN=token)
    return create_app(
        settings=settings,  # type: ignore[arg-type]
        lifespan=None,
        resource_overrides={
            "database": _Database(),
            "redis": _Redis(),
            "http": _Closeable(),
            "ssh_pool": _SSHPool(),
        },
    )


@pytest.mark.asyncio
async def test_metrics_endpoint_is_hidden_or_requires_its_own_bearer_token() -> None:
    disabled = _metrics_app(None)
    protected = _metrics_app("metrics-secret")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=disabled), base_url="http://test"
    ) as client:
        assert (await client.get("/metrics")).status_code == 404

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected), base_url="http://test"
    ) as client:
        await client.get("/livez", headers={"X-Request-ID": "metrics-request"})
        unauthorized = await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
        response = await client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert 'route="/livez"' in response.text
    assert "cs2_db_pool_checked_out 2" in response.text
    assert "cs2_redis_pool_in_use 1" in response.text
    assert "cs2_ssh_pool_in_use 1" in response.text


@pytest.mark.asyncio
async def test_task_supervisor_records_duration_and_failure_outcome() -> None:
    registry = MetricsRegistry()
    supervisor = TaskSupervisor("test", metrics_registry=registry)

    async def succeed() -> None:
        await asyncio.sleep(0)

    async def fail() -> None:
        raise RuntimeError("expected")

    successful = supervisor.create(succeed(), name="refresh-server-42")
    failed = supervisor.create(fail(), name="refresh-server-99", on_error=lambda *_: None)
    await asyncio.gather(successful, failed, return_exceptions=True)
    await asyncio.sleep(0)

    rendered = registry.render()
    assert 'name="refresh-server-#",outcome="success"} 1' in rendered
    assert 'name="refresh-server-#",outcome="error"} 1' in rendered
    assert "cs2_background_task_duration_seconds_sum" in rendered


def test_json_logging_always_contains_context_request_id() -> None:
    token = request_id_context.set("request-123")
    try:
        record = logging.LogRecord(
            name="cs2_manager.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="completed",
            args=(),
            exc_info=None,
        )
        record.http_status_code = 204
        RequestContextFilter().filter(record)
        payload = json.loads(JSONFormatter().format(record))
    finally:
        request_id_context.reset(token)

    assert payload["message"] == "completed"
    assert payload["request_id"] == "request-123"
    assert payload["http_status_code"] == 204


def test_production_logging_uses_json_stdout_only() -> None:
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    uvicorn_state = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
    }
    try:
        setup_logging(environment="production")
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JSONFormatter)
    finally:
        root.handlers.clear()
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)
        for name, (handlers, propagate) in uvicorn_state.items():
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.handlers.extend(handlers)
            logger.propagate = propagate
