"""Regression tests for deterministic lifecycle and task ownership."""

from __future__ import annotations

import asyncio

import pytest

from api import lifecycle as lifecycle_module
from api.lifecycle import ApplicationLifecycle
from services.task_registry import BackgroundTaskRegistry


@pytest.mark.asyncio
async def test_partial_startup_only_cleans_successful_services(monkeypatch):
    events: list[str] = []
    lifecycle = ApplicationLifecycle()

    async def runtime_cleanup():
        events.append("runtime")

    async def start_one():
        events.append("start-one")

    async def stop_one():
        events.append("stop-one")

    async def fail_two():
        events.append("start-two")
        raise RuntimeError("startup failed")

    async def stop_two():
        events.append("stop-two")

    monkeypatch.setattr(lifecycle_module, "_cleanup_runtime_tasks", runtime_cleanup)

    await lifecycle._start_service("one", start_one, stop_one)
    with pytest.raises(RuntimeError, match="startup failed"):
        await lifecycle._start_service("two", fail_two, stop_two)
    await lifecycle.stop()

    assert events == ["start-one", "start-two", "runtime", "stop-one"]


@pytest.mark.asyncio
async def test_lifecycle_cleanup_is_lifo_and_idempotent(monkeypatch):
    events: list[str] = []
    lifecycle = ApplicationLifecycle()

    async def runtime_cleanup():
        events.append("runtime")

    async def cleanup(name: str):
        events.append(name)

    monkeypatch.setattr(lifecycle_module, "_cleanup_runtime_tasks", runtime_cleanup)
    lifecycle._add_cleanup("first", lambda: cleanup("first"))
    lifecycle._add_cleanup("second", lambda: cleanup("second"))

    await lifecycle.stop()
    await lifecycle.stop()

    assert events == ["runtime", "second", "first", "runtime"]


@pytest.mark.asyncio
async def test_background_task_registry_cancels_and_forgets_tasks():
    registry = BackgroundTaskRegistry("test")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    registry.create(work())
    await started.wait()
    await registry.shutdown()

    assert cancelled.is_set()
    assert registry.tasks == set()


@pytest.mark.asyncio
async def test_background_task_registry_reports_failures():
    registry = BackgroundTaskRegistry("test")
    errors: list[BaseException] = []

    async def fail():
        raise ValueError("boom")

    task = registry.create(
        fail(),
        on_error=lambda _task, error: errors.append(error),
    )
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert registry.tasks == set()
