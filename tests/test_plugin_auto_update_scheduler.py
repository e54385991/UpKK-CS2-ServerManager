"""Regression tests for independent per-server plugin update scheduling."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from modules.models import AuthType, Server
from services import plugin_auto_update_service as module
from services.server_operation_hub import ServerOperationConflict


class _Session:
    def __init__(self, servers: list[Server]) -> None:
        self.servers = servers
        self.active = False

    async def __aenter__(self):
        self.active = True
        return self

    async def __aexit__(self, *_args):
        self.active = False

    async def execute(self, _query):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.servers))


@pytest.fixture
def scheduler(monkeypatch):
    service = module.PluginAutoUpdateService()
    servers = [
        Server(
            id=server_id,
            user_id=3,
            name=f"Server {server_id}",
            host="127.0.0.1",
            ssh_user="steam",
            auth_type=AuthType.PASSWORD,
            enable_plugin_auto_update=True,
            plugin_update_check_interval_hours=1.0,
            last_plugin_update_check=None,
            is_ssh_down=False,
        )
        for server_id in (7, 8)
    ]
    session = _Session(servers)
    monkeypatch.setattr(module, "async_session_maker", lambda: session)
    queue_check = AsyncMock(return_value=False)
    monkeypatch.setattr(service, "_plugin_update_already_queued", queue_check)

    async def enqueue(**_kwargs):
        assert not session.active, "Queue I/O must run after the database session closes"
        return {"operation_id": "op-1"}

    enqueuer = AsyncMock(side_effect=enqueue)
    monkeypatch.setattr("services.operation_enqueue.enqueue_plugin_auto_update", enqueuer)
    return service, servers, queue_check, enqueuer


@pytest.mark.parametrize("failure_stage", ["queue", "enqueue"])
@pytest.mark.asyncio
async def test_server_failure_does_not_starve_later_servers(scheduler, caplog, failure_stage):
    service, servers, queue_check, enqueue = scheduler
    failing_call = queue_check if failure_stage == "queue" else enqueue
    successful_result = False if failure_stage == "queue" else {"operation_id": "op-2"}
    failing_call.side_effect = [
        RuntimeError("server queue unavailable"),
        successful_result,
        RuntimeError("server queue unavailable"),
        successful_result,
    ]

    # A persistently failing first server must not prevent checks on server 8
    # in either pass; it must remain eligible for retry on the next pass.
    await service.check_all_servers()
    await service.check_all_servers()

    later_server_calls = [
        entry for entry in enqueue.await_args_list if entry.kwargs["server_id"] == 8
    ]
    assert later_server_calls == [
        call(server_id=8, actor_user_id=3, force=False),
        call(server_id=8, actor_user_id=3, force=False),
    ]
    assert all(server.last_plugin_update_check is None for server in servers)
    failures = [record for record in caplog.records if record.exc_info]
    assert len(failures) == 2
    assert all("server 7" in record.getMessage() for record in failures)

    failing_call.side_effect = None
    failing_call.return_value = successful_result
    enqueue.reset_mock()
    await service.check_all_servers()
    assert enqueue.await_args_list == [
        call(server_id=7, actor_user_id=3, force=False),
        call(server_id=8, actor_user_id=3, force=False),
    ]


@pytest.mark.asyncio
async def test_full_queue_does_not_block_other_servers(scheduler):
    service, _servers, _queue_check, enqueue = scheduler
    enqueue.side_effect = [ServerOperationConflict("queue full"), {"operation_id": "op-2"}]

    await service.check_all_servers()

    assert enqueue.await_args_list == [
        call(server_id=7, actor_user_id=3, force=False),
        call(server_id=8, actor_user_id=3, force=False),
    ]


@pytest.mark.parametrize("failure_stage", ["queue", "enqueue"])
@pytest.mark.asyncio
async def test_scheduler_cancellation_propagates(scheduler, failure_stage):
    service, _servers, queue_check, enqueue = scheduler
    failing_call = queue_check if failure_stage == "queue" else enqueue
    failing_call.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await service.check_all_servers()

    assert queue_check.await_count == 1
    assert enqueue.await_count == (0 if failure_stage == "queue" else 1)


@pytest.mark.parametrize("skip_reason", ["not_due", "ssh_down", "already_queued"])
@pytest.mark.asyncio
async def test_normal_skip_conditions_leave_other_servers_eligible(scheduler, skip_reason):
    service, servers, queue_check, enqueue = scheduler
    if skip_reason == "not_due":
        servers[0].last_plugin_update_check = module.get_current_time().replace(tzinfo=None)
    elif skip_reason == "ssh_down":
        servers[0].is_ssh_down = True
        servers[0].last_ssh_success = module.get_current_time() - timedelta(days=4)
    else:
        queue_check.side_effect = [True, False]

    await service.check_all_servers()

    enqueue.assert_awaited_once_with(server_id=8, actor_user_id=3, force=False)
