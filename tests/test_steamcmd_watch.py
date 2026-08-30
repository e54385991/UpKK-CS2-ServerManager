"""SteamCMD reconnect watch only starts for an active deploy/update."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import steamcmd_watch


@pytest.mark.asyncio
async def test_watch_skips_when_no_active_operation(monkeypatch):
    monkeypatch.setattr(
        steamcmd_watch.server_operation_hub,
        "get_current",
        AsyncMock(return_value=None),
    )
    started = []
    monkeypatch.setattr(
        steamcmd_watch,
        "_run_watch",
        AsyncMock(side_effect=lambda server: started.append(server.id)),
    )
    await steamcmd_watch.maybe_resume_steamcmd_watch(SimpleNamespace(id=2))
    assert started == []


@pytest.mark.asyncio
async def test_watch_starts_for_running_deploy(monkeypatch):
    monkeypatch.setattr(
        steamcmd_watch.server_operation_hub,
        "get_current",
        AsyncMock(return_value={"status": "running", "action": "deploy", "operation_id": "op-1"}),
    )
    started = []

    async def fake_run(server):
        started.append(server.id)

    monkeypatch.setattr(steamcmd_watch, "_run_watch", fake_run)
    steamcmd_watch._WATCHES.clear()
    await steamcmd_watch.maybe_resume_steamcmd_watch(SimpleNamespace(id=2))
    assert started == [2]
    assert 2 not in steamcmd_watch._WATCHES
