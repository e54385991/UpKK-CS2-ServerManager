"""Distributed security primitives must fail closed when Redis is unavailable."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from api.routes.file_manager import common as file_common
from services.maintenance_lock import (
    MaintenanceLockService,
    OperationCoordinationUnavailable,
)
from services.redis_manager import redis_manager


class _TicketRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.fail = fail

    async def set(self, key, value, **options):
        if self.fail:
            raise ConnectionError("redis unavailable")
        if options.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, _script, _key_count, key):
        if self.fail:
            raise ConnectionError("redis unavailable")
        return self.values.pop(key, None)


def _ticket_request(client) -> Request:
    container = SimpleNamespace(
        redis=SimpleNamespace(client=client),
        settings=file_common.settings,
    )
    app = SimpleNamespace(state=SimpleNamespace(container=container))
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_download_ticket_is_hashed_bound_and_consumed_once():
    client = _TicketRedis()
    request = _ticket_request(client)

    ticket = await file_common._create_download_ticket(request, 7, 11, "/maps/map.bsp")

    assert ticket not in "".join(client.values)
    assert all(ticket not in key for key in client.values)
    assert await file_common._consume_download_ticket(request, ticket, 11, "/maps/map.bsp") == 7
    assert await file_common._consume_download_ticket(request, ticket, 11, "/maps/map.bsp") is None


@pytest.mark.asyncio
async def test_download_ticket_mismatch_is_still_consumed():
    client = _TicketRedis()
    request = _ticket_request(client)
    ticket = await file_common._create_download_ticket(request, 7, 11, "/maps/map.bsp")

    assert await file_common._consume_download_ticket(request, ticket, 12, "/maps/map.bsp") is None
    assert await file_common._consume_download_ticket(request, ticket, 11, "/maps/map.bsp") is None


@pytest.mark.asyncio
async def test_download_tickets_fail_closed_when_redis_is_down():
    request = _ticket_request(_TicketRedis(fail=True))

    with pytest.raises(file_common.DownloadTicketStoreUnavailable):
        await file_common._create_download_ticket(request, 7, 11, "/maps/map.bsp")
    with pytest.raises(file_common.DownloadTicketStoreUnavailable):
        await file_common._consume_download_ticket(request, "ticket", 11, "/maps/map.bsp")


@pytest.mark.asyncio
async def test_destructive_lock_fails_closed_when_redis_is_down(monkeypatch):
    service = MaintenanceLockService()

    async def unavailable(*_args, **_kwargs):
        return None

    monkeypatch.setattr(redis_manager, "acquire_lock", unavailable)
    monkeypatch.setattr(redis_manager, "is_lock_held", unavailable)

    with pytest.raises(OperationCoordinationUnavailable):
        async with service.get(42, wait=False):
            pass
    with pytest.raises(OperationCoordinationUnavailable):
        await service.is_locked(42)
