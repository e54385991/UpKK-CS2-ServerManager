"""Per-server FIFO: a second submit waits instead of conflicting."""

from __future__ import annotations

import pytest

from services.server_operation_hub import ServerOperationHub


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
async def test_failed_job_is_retained_and_can_be_cleared(hub: ServerOperationHub):
    first = await hub.create(server_id=1, action="install_plugin", actor_user_id=1)
    await hub.finish(first["operation_id"], success=False, message="extract failed")
    failed = await hub.list_failed_for_server(1)
    assert [item["operation_id"] for item in failed] == [first["operation_id"]]
    assert failed[0]["command"] is None
    dismissed = await hub.dismiss_failed(first["operation_id"])
    assert dismissed is not None
    assert await hub.list_failed_for_server(1) == []
