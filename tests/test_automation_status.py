"""Live game observations, conservative restart eligibility, and scoped updates."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.v1 import plugin_updates
from services.automation import status as status_service
from tests.test_automation_auth import automation as automation


@pytest.fixture
def observation(monkeypatch):
    server = SimpleNamespace(id=12, status="running", manual_stop_requested=False)
    info = {
        "player_count": 0,
        "bot_count": 0,
        "max_players": 32,
        "version": "1.40.0.0",
        "map_name": "de_dust2",
    }
    probe = AsyncMock(return_value=(True, info))
    current = AsyncMock(return_value=None)
    locked = AsyncMock(return_value=False)
    monkeypatch.setattr(status_service.a2s_cache_service, "query_live_info", probe)
    monkeypatch.setattr(status_service.server_operation_hub, "get_current", current)
    monkeypatch.setattr(status_service.maintenance_lock_service, "is_locked", locked)
    return SimpleNamespace(server=server, info=info, probe=probe, current=current, locked=locked)


@pytest.mark.parametrize(
    ("players", "bots", "humans", "allowed"),
    [
        (0, 0, 0, True),
        (4, 4, 0, True),
        (3, 1, 2, False),
        (0, 1, None, False),
        (True, 0, None, False),
        (None, 0, None, False),
        (0, None, None, False),
        (-1, 0, None, False),
        (256, 0, None, False),
    ],
)
async def test_live_counts_never_turn_unknown_or_occupied_into_permission(
    observation, players, bots, humans, allowed
):
    state = observation
    state.info.update(player_count=players, bot_count=bots)
    result = await status_service.read_automation_status(state.server)
    assert result.human_count == humans
    assert result.restart_allowed is allowed
    assert result.version == "1.40.0.0"
    assert result.max_players == 32
    assert result.map_name == "de_dust2"
    state.probe.assert_awaited_once_with(state.server)


@pytest.mark.parametrize("blocker", ["manual_stop", "operation", "lock"])
async def test_zero_players_does_not_override_other_restart_restrictions(observation, blocker):
    state = observation
    if blocker == "manual_stop":
        state.server.manual_stop_requested = True
    elif blocker == "operation":
        state.current.return_value = {"status": "queued"}
    else:
        state.locked.return_value = True
    result = await status_service.read_automation_status(state.server)
    assert result.human_count == 0
    assert result.restart_allowed is False


async def test_failed_live_probe_never_reuses_returned_stale_counts_or_version(observation):
    state = observation
    state.probe.return_value = False, state.info
    result = await status_service.read_automation_status(state.server)
    assert result.query_ok is False
    assert result.player_count is None
    assert result.human_count is None
    assert result.version is None
    assert result.restart_allowed is False
    state.probe.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await status_service.read_automation_status(state.server)


def test_scoped_status_authorizes_before_probe_and_releases_the_request_session(
    automation, observation, monkeypatch
):
    state = automation
    close = AsyncMock()
    monkeypatch.setattr("api.routes.v1.automation.close_request_session", close)
    for headers in ({}, {"X-API-Key": "invalid"}):
        assert (
            state.client.get("/api/v1/servers/12/automation-status", headers=headers).status_code
            == 401
        )
    assert (
        state.client.get("/api/v1/servers/13/automation-status", headers=state.key).status_code
        == 404
    )
    observation.probe.assert_not_awaited()
    response = state.client.get("/api/v1/servers/12/automation-status", headers=state.key)
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.40.0.0"
    assert data["human_count"] == 0
    assert data["restart_allowed"] is True
    assert not {"host", "ssh_user", "game_directory", "api_key"} & data.keys()
    close.assert_awaited_once()


def test_scoped_plugin_update_job_uses_the_existing_fifo_and_audit(automation, monkeypatch):
    state = automation
    record = {**state.record, "action": "plugin_auto_update"}
    enqueue = AsyncMock(return_value=record)
    audit = AsyncMock()
    monkeypatch.setattr(plugin_updates, "enqueue_plugin_auto_update", enqueue)
    monkeypatch.setattr(plugin_updates, "reject_stuck_lock_unless_active", AsyncMock())
    monkeypatch.setattr(plugin_updates, "record_audit_event", audit)
    path = "/api/v1/servers/12/plugin-updates/run"
    assert state.client.post(path, headers={"X-API-Key": "invalid"}).status_code == 401
    assert state.client.post(path.replace("12", "13"), headers=state.key).status_code == 404
    enqueue.assert_not_awaited()
    response = state.client.post(path, headers=state.key)
    assert response.status_code == 202
    assert response.json()["action"] == "plugin_auto_update"
    enqueue.assert_awaited_once_with(server_id=12, actor_user_id=7, force=True)
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "plugin.auto_update.run"
