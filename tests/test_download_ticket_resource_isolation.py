"""Request-owned Redis isolation for one-time file download tickets."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.application import create_app
from api.routes.file_manager import common as file_common
from api.routes.file_manager import files as file_routes
from modules import get_current_active_user, get_db
from services.redis_manager import redis_manager


class _TicketClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, dict[str, object]]] = []
        self.eval_calls: list[tuple[str, str]] = []

    async def set(self, key, value, **options):
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.set_calls.append((key, options))
        if options.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, _key_count, key):
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.eval_calls.append((script, key))
        return self.values.pop(key, None)


class _UserResult:
    def __init__(self, user) -> None:
        self.user = user

    def scalar_one_or_none(self):
        return self.user


class _UserDatabase:
    def __init__(self, user) -> None:
        self.user = user

    async def execute(self, _statement):
        return _UserResult(self.user)


def _redis_resource(client):
    return SimpleNamespace(client=client)


def _request_for(app) -> Request:
    return Request({"type": "http", "app": app})


def _ticket_app(client):
    return create_app(
        lifespan=None,
        resource_overrides={"redis": _redis_resource(client)},
    )


@pytest.mark.asyncio
async def test_download_ticket_isolated_between_apps_and_replay_is_rejected(monkeypatch):
    first_client = _TicketClient()
    second_client = _TicketClient()
    first_app = _ticket_app(first_client)
    second_app = _ticket_app(second_client)
    current_user = SimpleNamespace(id=7, is_admin=False, is_active=True)
    server = SimpleNamespace(game_directory="/srv/cs2")

    # A process-global client must never be consulted by either isolated app.
    monkeypatch.setattr(redis_manager, "client", _TicketClient(fail=True))
    monkeypatch.setattr(
        file_routes,
        "get_server_for_user",
        AsyncMock(return_value=server),
    )
    first_app.dependency_overrides[get_db] = lambda: object()
    first_app.dependency_overrides[get_current_active_user] = lambda: current_user

    transport = httpx.ASGITransport(app=first_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://first") as client:
        issued = await client.post(
            "/servers/11/files/download-ticket",
            json={"path": "/srv/cs2/maps/map.bsp"},
        )

    assert issued.status_code == 200
    ticket = issued.json()["ticket"]
    assert issued.json()["expires_in"] == file_common.DOWNLOAD_TICKET_TTL_SECONDS
    assert len(first_client.values) == 1
    assert second_client.values == {}
    assert first_client.set_calls[0][1] == {
        "ex": file_common.DOWNLOAD_TICKET_TTL_SECONDS,
        "nx": True,
    }
    assert ticket not in next(iter(first_client.values))
    assert ticket not in next(iter(first_client.values.values()))

    with pytest.raises(HTTPException) as cross_app:
        await file_common.get_current_active_user_for_download(
            request=_request_for(second_app),
            server_id=11,
            path="/srv/cs2/maps/map.bsp",
            ticket=ticket,
            authorization=None,
            db=object(),
        )
    assert cross_app.value.status_code == 401

    authenticated = await file_common.get_current_active_user_for_download(
        request=_request_for(first_app),
        server_id=11,
        path="/srv/cs2/maps/map.bsp",
        ticket=ticket,
        authorization=None,
        db=_UserDatabase(current_user),
    )
    assert authenticated is current_user
    assert "redis.call('GET'" in first_client.eval_calls[0][0]
    assert "redis.call('DEL'" in first_client.eval_calls[0][0]

    with pytest.raises(HTTPException) as replay:
        await file_common.get_current_active_user_for_download(
            request=_request_for(first_app),
            server_id=11,
            path="/srv/cs2/maps/map.bsp",
            ticket=ticket,
            authorization=None,
            db=object(),
        )
    assert replay.value.status_code == 401


@pytest.mark.asyncio
async def test_download_ticket_missing_or_failed_app_redis_fails_closed():
    missing_app = SimpleNamespace(state=SimpleNamespace())
    failed_app = _ticket_app(_TicketClient(fail=True))

    for request in (_request_for(missing_app), _request_for(failed_app)):
        with pytest.raises(file_common.DownloadTicketStoreUnavailable) as create_error:
            await file_common._create_download_ticket(
                request,
                7,
                11,
                "/srv/cs2/maps/map.bsp",
            )
        assert str(create_error.value) == "Download ticket service is temporarily unavailable"

        with pytest.raises(file_common.DownloadTicketStoreUnavailable) as consume_error:
            await file_common._consume_download_ticket(
                request,
                "one-time-ticket",
                11,
                "/srv/cs2/maps/map.bsp",
            )
        assert str(consume_error.value) == "Download ticket service is temporarily unavailable"


@pytest.mark.asyncio
async def test_main_app_download_tickets_use_compatibility_container(monkeypatch):
    import main

    client = _TicketClient()
    assert main.app.state.container.redis is redis_manager
    monkeypatch.setattr(main.app.state.container.redis, "client", client)
    request = _request_for(main.app)

    ticket = await file_common._create_download_ticket(
        request,
        7,
        11,
        "/srv/cs2/maps/map.bsp",
    )

    assert (
        await file_common._consume_download_ticket(
            request,
            ticket,
            11,
            "/srv/cs2/maps/map.bsp",
        )
        == 7
    )
