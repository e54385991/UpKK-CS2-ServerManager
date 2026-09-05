"""Per-server FIFO: a second submit waits instead of conflicting."""

from __future__ import annotations

import asyncio

import pytest

from services.server_operation_hub import EVENT_LIMIT, ServerOperationHub


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
