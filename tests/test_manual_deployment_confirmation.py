"""Behavior checks for manually confirming a completed CS2 deployment."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routes.servers import maintenance
from modules import ServerStatus


class _FakeSession:
    def __init__(self):
        self.added = []
        self.committed = False
        self.refreshed = []

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        self.refreshed.append(item)


@pytest.mark.asyncio
async def test_manual_deployment_confirmation_records_completion_and_stops_server(monkeypatch):
    server = SimpleNamespace(status=ServerStatus.ERROR, last_deployed=None)
    db = _FakeSession()
    cached_statuses = []

    async def get_server(*_args):
        return server

    async def get_lock(_key):
        return None

    async def set_status(server_id, status):
        cached_statuses.append((server_id, status))
        return True

    monkeypatch.setattr(maintenance, "get_server_with_permission", get_server)
    monkeypatch.setattr(maintenance.redis_manager, "get", get_lock)
    monkeypatch.setattr(maintenance.redis_manager, "set_server_status", set_status)

    result = await maintenance.confirm_server_deployment(
        42, db=db, current_user=SimpleNamespace(id=7)
    )

    assert result["success"] is True
    assert result["status"] == ServerStatus.STOPPED.value
    assert server.status == ServerStatus.STOPPED
    assert server.last_deployed is not None
    assert db.committed is True
    assert db.added[0].action == "manual_deployment_confirmation"
    assert cached_statuses == [(42, ServerStatus.STOPPED.value)]


@pytest.mark.asyncio
async def test_manual_deployment_confirmation_rejects_active_deployment(monkeypatch):
    async def get_server(*_args):
        return SimpleNamespace(status=ServerStatus.DEPLOYING, last_deployed=None)

    async def get_lock(_key):
        return "1"

    monkeypatch.setattr(maintenance, "get_server_with_permission", get_server)
    monkeypatch.setattr(maintenance.redis_manager, "get", get_lock)

    with pytest.raises(HTTPException) as exc_info:
        await maintenance.confirm_server_deployment(
            42, db=_FakeSession(), current_user=SimpleNamespace(id=7)
        )

    assert exc_info.value.status_code == 409
