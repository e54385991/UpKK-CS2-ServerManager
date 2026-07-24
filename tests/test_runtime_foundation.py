"""Focused coverage for the per-application runtime boundary."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from api.application import create_app
from cs2_manager.core import Principal
from cs2_manager.infrastructure import UnitOfWork
from cs2_manager.runtime import TaskSupervisor


class FakeDatabase:
    session_factory = None

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def ping(self) -> bool:
        if not self.ready:
            raise ConnectionError("database unavailable")
        return True


class FakeRedis:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def ping(self) -> bool:
        return self.ready


class FakeCloseable:
    async def close(self) -> None:
        pass


def runtime_app(*, database_ready: bool = True, redis_ready: bool = True):
    return create_app(
        lifespan=None,
        resource_overrides={
            "database": FakeDatabase(database_ready),
            "redis": FakeRedis(redis_ready),
            "http": FakeCloseable(),
            "ssh_pool": None,
        },
    )


@pytest.mark.asyncio
async def test_health_readiness_and_request_id_use_app_resources():
    app = runtime_app(database_ready=True, redis_ready=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health", headers={"X-Request-ID": "runtime-test"})
        readiness = await client.get("/readyz")
        liveness = await client.get("/livez")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.headers["X-Request-ID"] == "runtime-test"
    assert readiness.status_code == 503
    assert readiness.json()["redis"] == "disconnected"
    assert readiness.json()["migrations"] == "current"
    assert readiness.json()["runtime"] == "ready"
    assert liveness.status_code == 200


@pytest.mark.asyncio
async def test_readiness_fails_closed_when_database_revision_is_outdated(monkeypatch):
    app = runtime_app()
    app.state.container.database.engine = object()

    async def outdated(_engine):
        return type("MigrationStatus", (), {"is_current": False})()

    monkeypatch.setattr(
        "cs2_manager.infrastructure.migrations.get_migration_status",
        outdated,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        readiness = await client.get("/readyz")

    assert readiness.status_code == 503
    assert readiness.json()["migrations"] == "outdated"


def test_application_factory_containers_and_overrides_are_isolated():
    first_database = FakeDatabase()
    second_database = FakeDatabase()
    first = create_app(lifespan=None, resource_overrides={"database": first_database})
    second = create_app(lifespan=None, resource_overrides={"database": second_database})

    assert first.state.container is not second.state.container
    assert first.state.container.database is first_database
    assert second.state.container.database is second_database
    assert first.state.task_supervisor is not second.state.task_supervisor


@pytest.mark.asyncio
async def test_task_supervisor_cancels_owned_work():
    supervisor = TaskSupervisor("test")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def work() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    supervisor.create(work(), name="owned-work")
    await started.wait()
    await supervisor.shutdown()

    assert cancelled.is_set()
    assert not supervisor.tasks


def test_principal_is_detached_and_immutable():
    principal = Principal(
        id=7,
        username="operator",
        email="operator@example.com",
        is_admin=True,
    )

    assert principal.user_id == 7
    with pytest.raises(ValidationError):
        principal.username = "changed"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_uncommitted_sessions():
    class Session:
        committed = False
        rolled_back = False
        closed = False

        async def commit(self) -> None:
            self.committed = True

        async def rollback(self) -> None:
            self.rolled_back = True

        async def close(self) -> None:
            self.closed = True

    session = Session()
    async with UnitOfWork(lambda: session):  # type: ignore[arg-type]
        pass

    assert session.rolled_back is True
    assert session.closed is True
