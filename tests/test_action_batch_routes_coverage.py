"""Coverage for batch action request validation and status summaries."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.routes.actions import batch


def _user(**overrides):
    values = {"id": 5, "is_admin": False}
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_batch(monkeypatch, valid=(1, 2)):
    redis = SimpleNamespace(
        set_batch_action_statuses=AsyncMock(),
        get_batch_action_status=AsyncMock(),
    )
    monkeypatch.setattr(batch, "redis_manager", redis)
    monkeypatch.setattr(batch, "authorized_server_ids", AsyncMock(return_value=list(valid)))
    monkeypatch.setattr(batch, "_reserve_batch_capacity", AsyncMock())
    monkeypatch.setattr(batch, "_store_task", Mock())

    def discard_task(coroutine):
        coroutine.close()
        return SimpleNamespace()

    monkeypatch.setattr(batch.asyncio, "create_task", Mock(side_effect=discard_task))
    monkeypatch.setattr(batch, "record_audit_event", AsyncMock())
    monkeypatch.setattr(batch.secrets, "token_hex", lambda _size: "batch-id")
    return redis


@pytest.mark.asyncio
async def test_batch_action_plugins_and_command_endpoints_queue_and_audit(monkeypatch):
    _patch_batch(monkeypatch)
    user = _user()
    request = SimpleNamespace(server_ids=[1, 2], action="start")
    result = await batch.batch_server_actions(
        request, db=None, current_user=user, http_request=object()
    )
    assert result.batch_id == "batch-id"
    assert result.server_count == 2
    assert "start" in result.message

    plugin_result = await batch.batch_install_plugins(
        SimpleNamespace(server_ids=[1], plugins=["metamod"]),
        db=None,
        current_user=user,
        http_request=object(),
    )
    assert plugin_result.server_count == 2
    command_result = await batch.batch_send_command(
        SimpleNamespace(server_ids=[2], command="status"),
        db=None,
        current_user=user,
        http_request=object(),
    )
    assert command_result.batch_id == "batch-id"
    assert batch._store_task.call_count == 6

    monkeypatch.setattr(batch, "authorized_server_ids", AsyncMock(return_value=[]))
    for handler, request in (
        (batch.batch_server_actions, SimpleNamespace(server_ids=[1], action="start")),
        (batch.batch_install_plugins, SimpleNamespace(server_ids=[1], plugins=["metamod"])),
        (batch.batch_send_command, SimpleNamespace(server_ids=[1], command="status")),
    ):
        with pytest.raises(HTTPException) as caught:
            await handler(request, db=None, current_user=user, http_request=object())
        assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_batch_status_endpoint_summarizes_and_handles_expired_status(monkeypatch):
    redis = _patch_batch(monkeypatch)
    redis.get_batch_action_status.return_value = {
        "1": {"status": "success"},
        "2": {"status": "failed"},
        "3": {"status": "pending"},
        "4": {"status": "in_progress"},
        "5": {"status": "other"},
    }
    result = await batch.get_batch_action_status("batch-id", _user())
    assert result["summary"] == {
        "total": 5,
        "completed": 2,
        "succeeded": 1,
        "failed": 1,
        "in_progress": 2,
        "is_complete": False,
    }
    redis.get_batch_action_status.return_value = None
    with pytest.raises(HTTPException) as caught:
        await batch.get_batch_action_status("expired", _user())
    assert caught.value.status_code == 404
