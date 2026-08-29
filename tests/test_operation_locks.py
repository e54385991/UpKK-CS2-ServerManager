"""Stuck-lock checks must not 409 when a hub job is already serializing the server."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.v1.operation_locks import reject_stuck_lock_unless_active


@pytest.mark.asyncio
async def test_active_hub_job_skips_stuck_lock(monkeypatch):
    monkeypatch.setattr(
        "api.routes.v1.operation_locks.server_operation_hub.get_current",
        AsyncMock(return_value={"status": "running", "operation_id": "live"}),
    )
    redis_get = AsyncMock(return_value="1")
    monkeypatch.setattr("api.routes.v1.operation_locks.redis_manager.get", redis_get)
    await reject_stuck_lock_unless_active(1)
    redis_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_stuck_lock_without_hub_job_conflicts(monkeypatch):
    monkeypatch.setattr(
        "api.routes.v1.operation_locks.server_operation_hub.get_current",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routes.v1.operation_locks.redis_manager.get",
        AsyncMock(return_value="1"),
    )
    with pytest.raises(HTTPException) as exc:
        await reject_stuck_lock_unless_active(1)
    assert exc.value.status_code == 409
