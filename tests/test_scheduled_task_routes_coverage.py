"""Isolated coverage for the legacy scheduled-task routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import scheduled_tasks as routes


def _task(**overrides):
    values = {
        "id": 9,
        "server_id": 4,
        "name": "nightly restart",
        "action": "restart",
        "enabled": True,
        "schedule_type": "interval",
        "schedule_value": "3600",
        "next_run": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Db:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.added = []
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()

    def add(self, value):
        self.added.append(value)

    async def execute(self, _statement):
        return SimpleNamespace(rowcount=self.rowcount)


def _patch_access(monkeypatch, server=None):
    monkeypatch.setattr(
        routes,
        "get_server_for_user",
        AsyncMock(return_value=server or SimpleNamespace(id=4)),
    )


@pytest.mark.asyncio
async def test_scheduled_task_helpers_and_create_list_get(monkeypatch):
    with pytest.raises(HTTPException) as caught:
        routes._require_user_managed_task(_task(action="log_cleanup"))
    assert caught.value.status_code == 404
    assert routes._require_user_managed_task(_task()) .action == "restart"

    _patch_access(monkeypatch)
    db = _Db()
    recalculate = AsyncMock()
    monkeypatch.setattr(routes.scheduled_task_service, "recalculate_next_run", recalculate)
    created = await routes.create_scheduled_task(
        4,
        routes.ScheduledTaskCreate(
            name="nightly restart",
            action="restart",
            enabled=True,
            schedule_type="interval",
            schedule_value="3600",
        ),
        db,
        SimpleNamespace(id=2, is_admin=False),
    )
    assert created in db.added
    assert created.server_id == 4
    recalculate.assert_awaited_once_with(created.id)
    assert db.commit.await_count == 1
    assert db.refresh.await_count == 2

    visible = _task(action="restart")
    hidden = _task(id=10, action="map_pool_sync")
    monkeypatch.setattr(routes.ScheduledTask, "get_all_by_server", AsyncMock(return_value=[visible, hidden]))
    listed = await routes.list_scheduled_tasks(4, db, SimpleNamespace(id=2, is_admin=False))
    assert listed == [visible]

    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=visible))
    assert await routes.get_scheduled_task(4, 9, db, SimpleNamespace(id=2)) is visible
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await routes.get_scheduled_task(4, 9, db, SimpleNamespace(id=2))
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_scheduled_task_access_helper_handles_admin_owner_and_missing(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    owner = SimpleNamespace(id=4)
    monkeypatch.setattr(routes.Server, "get_by_id_and_user", AsyncMock(return_value=owner))
    assert await routes.get_server_for_user(4, db, SimpleNamespace(id=2, is_admin=False)) is owner
    routes.Server.get_by_id_and_user.assert_awaited_once_with(db, 4, 2)
    monkeypatch.setattr(routes.Server, "get_by_id", AsyncMock(return_value=owner))
    assert await routes.get_server_for_user(4, db, SimpleNamespace(id=1, is_admin=True)) is owner
    routes.Server.get_by_id.assert_awaited_once_with(db, 4)

    monkeypatch.setattr(routes.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await routes.get_server_for_user(4, db, SimpleNamespace(id=2, is_admin=False))
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_scheduled_task_update_covers_schedule_recalculation_and_errors(monkeypatch):
    _patch_access(monkeypatch)
    db = _Db()
    task = _task()
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=task))
    next_run = datetime(2026, 9, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(routes.scheduled_task_service, "_calculate_next_run", lambda value: next_run)
    result = await routes.update_scheduled_task(
        4,
        9,
        routes.ScheduledTaskUpdate(schedule_type="daily", schedule_value="03:30"),
        db,
        SimpleNamespace(id=2),
    )
    assert result is task
    assert task.schedule_type == "daily"
    assert task.next_run == next_run

    task = _task()
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=task))
    result = await routes.update_scheduled_task(
        4, 9, routes.ScheduledTaskUpdate(name="changed", enabled=False), db, SimpleNamespace(id=2)
    )
    assert result.name == "changed"
    assert result.enabled is False

    task = _task()
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=task))
    monkeypatch.setattr(routes.scheduled_task_service, "_calculate_next_run", lambda _task: None)
    with pytest.raises(HTTPException) as caught:
        await routes.update_scheduled_task(
            4,
            9,
            routes.ScheduledTaskUpdate(schedule_type="daily", schedule_value="03:30"),
            db,
            SimpleNamespace(id=2),
        )
    assert caught.value.status_code == 400
    assert db.rollback.await_count >= 1

    task = _task()
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=task))
    monkeypatch.setattr(
        routes.scheduled_task_service,
        "_calculate_next_run",
        lambda _task: (_ for _ in ()).throw(RuntimeError("bad schedule")),
    )
    with pytest.raises(HTTPException) as caught:
        await routes.update_scheduled_task(
            4,
            9,
            routes.ScheduledTaskUpdate(schedule_type="daily", schedule_value="03:30"),
            db,
            SimpleNamespace(id=2),
        )
    assert caught.value.status_code == 400
    assert "bad schedule" in str(caught.value.detail)

    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await routes.update_scheduled_task(
            4, 9, routes.ScheduledTaskUpdate(name="x"), db, SimpleNamespace(id=2)
        )
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_scheduled_task_delete_and_toggle_cover_conflicts_and_states(monkeypatch):
    _patch_access(monkeypatch)
    db = _Db(rowcount=1)
    task = _task(enabled=False)
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=task))
    deleted = await routes.delete_scheduled_task(4, 9, db, SimpleNamespace(id=2))
    assert deleted["success"] is True

    db = _Db(rowcount=0)
    with pytest.raises(HTTPException) as caught:
        await routes.delete_scheduled_task(4, 9, db, SimpleNamespace(id=2))
    assert caught.value.status_code == 404
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await routes.delete_scheduled_task(4, 9, db, SimpleNamespace(id=2))
    assert caught.value.status_code == 404

    hidden = _task(action="log_cleanup")
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=hidden))
    with pytest.raises(HTTPException) as caught:
        await routes.delete_scheduled_task(4, 9, db, SimpleNamespace(id=2))
    assert caught.value.status_code == 404

    recalculate = AsyncMock()
    monkeypatch.setattr(routes.scheduled_task_service, "recalculate_next_run", recalculate)
    task = _task(enabled=False)
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=task))
    result = await routes.toggle_scheduled_task(4, 9, db, SimpleNamespace(id=2))
    assert result.enabled is True
    recalculate.assert_awaited_once_with(9)

    task = _task(enabled=True)
    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=task))
    result = await routes.toggle_scheduled_task(4, 9, db, SimpleNamespace(id=2))
    assert result.enabled is False
    assert recalculate.await_count == 1

    monkeypatch.setattr(routes.ScheduledTask, "get_by_id_and_server", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await routes.toggle_scheduled_task(4, 9, db, SimpleNamespace(id=2))
    assert caught.value.status_code == 404
