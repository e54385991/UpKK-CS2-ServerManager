"""Per-server FIFO: a second submit waits instead of conflicting."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.server_operation_hub import EVENT_LIMIT, TERMINAL_EVENT_TYPES, ServerOperationHub


@pytest.fixture
def hub(monkeypatch) -> ServerOperationHub:
    instance = ServerOperationHub()

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(instance, "_persist_record", noop)
    monkeypatch.setattr(instance, "_persist_event", noop)
    monkeypatch.setattr(instance, "_persist_pending", noop)
    monkeypatch.setattr(instance, "_persist_failed", noop)
    monkeypatch.setattr(instance, "_expire_events", noop)
    monkeypatch.setattr(instance, "_forget_operation", noop)
    monkeypatch.setattr("services.server_operation_hub.redis_manager.set", noop)
    monkeypatch.setattr("services.server_operation_hub.redis_manager.get", noop)
    monkeypatch.setattr("services.server_operation_hub.redis_manager.delete", noop)
    return instance


@pytest.mark.asyncio
async def test_second_create_queues_behind_active(hub: ServerOperationHub):
    first = await hub.create(server_id=1, action="install_plugin", actor_user_id=1)
    await hub.mark_running(first["operation_id"])
    second = await hub.create(
        server_id=1,
        action="install_plugin",
        actor_user_id=1,
        command="plugin-market install 11 --from latest",
    )
    current = await hub.get_current(1)
    assert current is not None
    assert current["operation_id"] == first["operation_id"]
    assert second["status"] == "queued"
    assert second["command"] == "plugin-market install 11 --from latest"
    listed = await hub.list_for_server(1)
    assert [item["operation_id"] for item in listed] == [
        first["operation_id"],
        second["operation_id"],
    ]


@pytest.mark.asyncio
async def test_finish_promotes_pending_worker(hub: ServerOperationHub):
    started: list[str] = []

    def fake_start(operation_id: str, factory=None) -> None:
        started.append(operation_id)

    hub._start = fake_start  # type: ignore[method-assign]
    first = await hub.create(server_id=1, action="start", actor_user_id=1)
    second = await hub.create(server_id=1, action="install_plugin", actor_user_id=1)
    await hub.schedule(first["operation_id"], lambda: None)
    await hub.schedule(second["operation_id"], lambda: None)
    await hub.finish(first["operation_id"], success=True, message="done")
    current = await hub.get_current(1)
    assert current is not None
    assert current["operation_id"] == second["operation_id"]
    assert started[-1] == second["operation_id"]
    listed = await hub.list_for_server(1)
    assert [item["operation_id"] for item in listed] == [
        second["operation_id"],
        first["operation_id"],
    ]


@pytest.mark.asyncio
async def test_cancel_queued_operation_removes_it_without_disturbing_current(
    hub: ServerOperationHub,
):
    first = await hub.create(server_id=1, action="start", actor_user_id=1)
    second = await hub.create(server_id=1, action="install_plugin", actor_user_id=1)
    third = await hub.create(server_id=1, action="update", actor_user_id=1)

    cancelled = await hub.cancel(second["operation_id"], message="force stopped")

    assert cancelled is not None
    assert cancelled["status"] == "failed"
    assert cancelled["message"] == "force stopped"
    assert await hub.get_current(1) == first
    assert hub._pending[1] == [third["operation_id"]]
    assert [item["operation_id"] for item in await hub.list_failed_for_server(1)] == [
        second["operation_id"]
    ]


@pytest.mark.asyncio
async def test_cancel_running_operation_cancels_task_and_promotes_next(hub: ServerOperationHub):
    first = await hub.create(server_id=1, action="start", actor_user_id=1)
    second = await hub.create(server_id=1, action="update", actor_user_id=1)
    await hub.mark_running(first["operation_id"])
    task = asyncio.create_task(asyncio.sleep(60))
    hub._tasks[first["operation_id"]] = task
    started: list[str] = []
    hub._runners[second["operation_id"]] = lambda: None
    hub._start = lambda operation_id, _factory=None: started.append(operation_id)  # type: ignore[method-assign]

    cancelled = await hub.cancel(first["operation_id"], message="force stopped")
    await asyncio.sleep(0)

    assert cancelled is not None
    assert cancelled["status"] == "failed"
    assert task.cancelled() is True
    assert started == [second["operation_id"]]


@pytest.mark.asyncio
async def test_failed_job_is_retained_and_can_be_cleared(hub: ServerOperationHub):
    first = await hub.create(server_id=1, action="install_plugin", actor_user_id=1)
    await hub.finish(first["operation_id"], success=False, message="extract failed")
    failed = await hub.list_failed_for_server(1)
    assert [item["operation_id"] for item in failed] == [first["operation_id"]]
    assert failed[0]["command"] is None
    dismissed = await hub.dismiss_failed(first["operation_id"])
    assert dismissed is not None
    assert await hub.list_failed_for_server(1) == []


@pytest.mark.asyncio
async def test_completed_job_is_retained_with_replayable_history_and_can_be_cleared(
    hub: ServerOperationHub,
):
    record = await hub.create(server_id=1, action="install_plugin", actor_user_id=1)
    operation_id = record["operation_id"]
    await hub.emit(operation_id, "progress", kind="output", message="installed files")
    await hub.finish(operation_id, success=True, message="Plugin installed")

    completed = await hub.list_completed_for_server(1)
    assert [item["operation_id"] for item in completed] == [operation_id]
    assert completed[0]["status"] == "completed"
    assert [event["message"] for event in await hub.replay(operation_id)] == [
        "Operation accepted: install_plugin (queued)",
        "installed files",
        "Plugin installed",
    ]

    dismissed = await hub.dismiss_completed(operation_id)
    assert dismissed is not None
    assert await hub.list_completed_for_server(1) == []


@pytest.mark.asyncio
async def test_emit_keeps_only_the_latest_event_limit(hub, monkeypatch):
    monkeypatch.setattr("services.server_operation_hub.EVENT_LIMIT", 3)
    record = await hub.create(server_id=1, action="deploy", actor_user_id=1)
    operation_id = record["operation_id"]
    for index in range(5):
        await hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=f"line-{index}",
        )
    assert [event["message"] for event in hub._events[operation_id]] == [
        "line-2",
        "line-3",
        "line-4",
    ]
    assert EVENT_LIMIT == 300


@pytest.mark.asyncio
async def test_wait_until_terminal_returns_already_finished_record(hub: ServerOperationHub):
    record = await hub.create(server_id=1, action="stop", actor_user_id=1)
    await hub.finish(record["operation_id"], success=True, message="stopped")
    waited = await hub.wait_until_terminal(record["operation_id"])
    assert waited["operation_id"] == record["operation_id"]
    assert waited["status"] == "completed"
    assert waited["success"] is True


@pytest.mark.asyncio
async def test_wait_until_terminal_subscribes_until_finish(hub: ServerOperationHub):
    record = await hub.create(server_id=1, action="stop", actor_user_id=1)
    operation_id = record["operation_id"]
    waiting = asyncio.create_task(hub.wait_until_terminal(operation_id))
    for _ in range(50):
        if hub._queues.get(operation_id):
            break
        await asyncio.sleep(0.01)
    else:
        waiting.cancel()
        raise AssertionError("wait_until_terminal never subscribed")
    await hub.finish(operation_id, success=False, message="timed out")
    final = await waiting
    assert final["status"] == "failed"
    assert final["success"] is False
    assert final["message"] == "timed out"


def _record_starts(hub: ServerOperationHub) -> list[tuple[str, asyncio.Task]]:
    """Replace the worker launcher with one that binds a cancellable stand-in task."""
    started: list[tuple[str, asyncio.Task]] = []

    def fake_start(operation_id: str, _factory=None) -> None:
        task = asyncio.create_task(asyncio.sleep(60))
        hub.bind_task(operation_id, task)
        started.append((operation_id, task))

    hub._start = fake_start  # type: ignore[method-assign]
    return started


def _terminal_events(queue: asyncio.Queue) -> list[str]:
    events: list[str] = []
    while not queue.empty():
        event = queue.get_nowait()
        if event["type"] in TERMINAL_EVENT_TYPES:
            events.append(event["type"])
    return events


async def _cancel_started(started: list[tuple[str, asyncio.Task]]) -> None:
    for _operation_id, task in started:
        task.cancel()
    await asyncio.gather(*(task for _operation_id, task in started), return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_finishers_record_one_outcome_and_promote_one_job(
    hub: ServerOperationHub,
):
    started = _record_starts(hub)
    first = await hub.create(server_id=1, action="start", actor_user_id=1)
    second = await hub.create(server_id=1, action="install_plugin", actor_user_id=1)
    third = await hub.create(server_id=1, action="update", actor_user_id=1)
    await hub.schedule(second["operation_id"], lambda: None)
    await hub.schedule(third["operation_id"], lambda: None)
    await hub.mark_running(first["operation_id"])
    events = await hub.subscribe_queue(first["operation_id"])

    # Another server's create() can hold the shared queue lock across a Redis
    # read. The worker's own completion and a force-stop then both pass their
    # status check before either one records an outcome.
    async with hub._lock:
        worker = asyncio.create_task(
            hub.finish(first["operation_id"], success=True, message="done")
        )
        stopper = asyncio.create_task(
            hub.finish(
                first["operation_id"], success=False, message="force stopped", cancelled=True
            )
        )
        await asyncio.sleep(0)
    finished = await asyncio.gather(worker, stopper)

    try:
        assert _terminal_events(events) == ["operation_completed"]
        assert [record["status"] for record in finished] == ["completed", "completed"]
        assert [operation_id for operation_id, _task in started] == [second["operation_id"]]
        current = await hub.get_current(1)
        assert current is not None and current["operation_id"] == second["operation_id"]
        assert hub._pending[1] == [third["operation_id"]]
    finally:
        await _cancel_started(started)


@pytest.mark.asyncio
async def test_cancel_during_promotion_stops_the_promoted_job(hub, monkeypatch):
    started = _record_starts(hub)
    first = await hub.create(server_id=1, action="start", actor_user_id=1)
    second = await hub.create(server_id=1, action="update", actor_user_id=1)
    await hub.schedule(second["operation_id"], lambda: None)
    await hub.mark_running(first["operation_id"])
    persisting = asyncio.Event()
    release = asyncio.Event()

    async def slow_persist_pending(_server_id: int) -> None:
        persisting.set()
        await release.wait()

    monkeypatch.setattr(hub, "_persist_pending", slow_persist_pending)
    finishing = asyncio.create_task(hub.finish(first["operation_id"], success=True, message="ok"))
    await persisting.wait()  # the promotion is writing the shortened queue to Redis
    try:
        cancelled = await hub.cancel(second["operation_id"], message="force stopped")
    finally:
        release.set()
        await finishing

    try:
        assert cancelled is not None and cancelled["status"] == "failed"
        stored = await hub.get(second["operation_id"])
        assert stored is not None and stored["status"] == "failed"
        await asyncio.sleep(0)
        assert [operation_id for operation_id, _task in started] == [second["operation_id"]]
        assert started[0][1].cancelled() is True
    finally:
        await _cancel_started(started)


@pytest.mark.asyncio
async def test_job_promoted_before_its_runner_is_registered_still_runs(hub, monkeypatch):
    started = _record_starts(hub)
    first = await hub.create(server_id=1, action="start", actor_user_id=1)
    await hub.mark_running(first["operation_id"])
    persisting = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def persist_pending(_server_id: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:  # only the new job's create() stalls on Redis
            persisting.set()
            await release.wait()

    monkeypatch.setattr(hub, "_persist_pending", persist_pending)
    creating = asyncio.create_task(
        hub.create(server_id=1, action="install_plugin", actor_user_id=1)
    )
    await persisting.wait()  # queued in memory; the caller has no record to schedule yet
    try:
        await hub.finish(first["operation_id"], success=True, message="done")
    finally:
        release.set()
    second = await creating
    await hub.schedule(second["operation_id"], lambda: None)

    try:
        record = await hub.get(second["operation_id"])
        assert record is not None and record["status"] == "queued"
        assert await hub.list_failed_for_server(1) == []
        assert [operation_id for operation_id, _task in started] == [second["operation_id"]]
    finally:
        await _cancel_started(started)


@pytest.mark.asyncio
async def test_schedule_does_not_start_a_job_that_already_finished(hub: ServerOperationHub):
    started = _record_starts(hub)
    record = await hub.create(server_id=1, action="start", actor_user_id=1)
    await hub.cancel(record["operation_id"], message="force stopped")

    await hub.schedule(record["operation_id"], lambda: None)

    stored = await hub.get(record["operation_id"])
    assert stored is not None and stored["status"] == "failed"
    assert started == []


@pytest.mark.asyncio
async def test_cancelled_create_does_not_leave_a_job_blocking_the_queue(hub, monkeypatch):
    stalled = asyncio.Event()
    calls = 0

    async def persist_record(_record) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:  # the first write of the new record hangs until cancelled
            stalled.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(hub, "_persist_record", persist_record)
    creating = asyncio.create_task(hub.create(server_id=1, action="start", actor_user_id=1))
    await stalled.wait()
    creating.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creating

    abandoned = await hub.get_current(1)
    assert abandoned is not None and abandoned["status"] == "failed"
    replacement = await hub.create(server_id=1, action="update", actor_user_id=1)
    current = await hub.get_current(1)
    assert current is not None and current["operation_id"] == replacement["operation_id"]


@pytest.mark.asyncio
async def test_finished_jobs_leave_process_memory_once_history_drops_them(hub, monkeypatch):
    monkeypatch.setattr("services.server_operation_history.MAX_COMPLETED_PER_SERVER", 2)
    started = _record_starts(hub)
    finished: list[str] = []
    try:
        for action in ("start", "stop", "update"):
            record = await hub.create(server_id=1, action=action, actor_user_id=1)
            operation_id = record["operation_id"]
            await hub.schedule(operation_id, lambda: None)
            await hub.finish(operation_id, success=True, message=f"{action} done")
            finished.append(operation_id)
    finally:
        await _cancel_started(started)

    oldest, *retained = finished
    assert oldest not in hub._records
    assert oldest not in hub._events
    assert all(operation_id in hub._records for operation_id in retained)
    listed = await hub.list_completed_for_server(1)
    assert {item["operation_id"] for item in listed} == set(retained)
    assert hub._runners == {}  # a finished job's runner can never be started again


@pytest.mark.asyncio
async def test_expired_history_entries_are_released_from_memory(hub: ServerOperationHub):
    record = await hub.create(server_id=1, action="start", actor_user_id=1)
    operation_id = record["operation_id"]
    await hub.finish(operation_id, success=False, message="extract failed")
    expired = datetime.now(timezone.utc) - timedelta(days=8)
    hub._records[operation_id]["completed_at"] = expired.isoformat()

    assert await hub.list_failed_for_server(1) == []
    assert operation_id not in hub._records
    assert operation_id not in hub._events
