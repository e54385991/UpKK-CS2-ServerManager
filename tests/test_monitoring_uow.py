"""Principal, repository, and transaction boundaries for monitoring routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.dependencies import get_unit_of_work
from api.routes.servers import monitoring
from cs2_manager.core import Principal
from cs2_manager.features.servers import (
    ServerMonitoringRepository,
    ServerNotFoundError,
)
from modules.auth import create_access_token, get_current_principal


class _Result:
    def __init__(
        self,
        *,
        ids: list[int] | None = None,
        row: object | None = None,
    ) -> None:
        self._ids = ids or []
        self._row = row

    def scalars(self):
        return self

    def all(self) -> list[int]:
        return self._ids

    def one_or_none(self):
        return self._row


class _Session:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.result


def _principal(*, admin: bool = False) -> Principal:
    return Principal(
        id=7,
        username="admin" if admin else "owner",
        email="user@example.com",
        is_admin=admin,
    )


def _target_row() -> object:
    return SimpleNamespace(
        _mapping={
            "id": 31,
            "host": "server.example.com",
            "game_port": 27015,
            "a2s_query_host": "query.example.com",
            "a2s_query_port": 27016,
        }
    )


@pytest.mark.asyncio
async def test_monitoring_repository_filters_owner_but_not_admin() -> None:
    owner_session = _Session(_Result(ids=[31, 32]))
    admin_session = _Session(_Result(ids=[31, 32]))

    assert await ServerMonitoringRepository(owner_session).visible_server_ids(_principal()) == [
        31,
        32,
    ]
    assert await ServerMonitoringRepository(admin_session).visible_server_ids(
        _principal(admin=True)
    ) == [31, 32]

    assert "servers.user_id" in str(owner_session.statements[0])
    assert "servers.user_id" not in str(admin_session.statements[0])


@pytest.mark.asyncio
async def test_monitoring_repository_returns_detached_target_and_maps_missing() -> None:
    session = _Session(_Result(row=_target_row()))
    target = await ServerMonitoringRepository(session).require_a2s_target(31, _principal())

    assert target.id == 31
    assert target.query_host == "query.example.com"
    assert target.query_port == 27016
    assert "servers.user_id" in str(session.statements[0])

    missing = ServerMonitoringRepository(_Session(_Result()))
    with pytest.raises(ServerNotFoundError, match="Server not found"):
        await missing.require_a2s_target(404, _principal())


@pytest.mark.asyncio
async def test_live_a2s_query_commits_before_remote_io(monkeypatch) -> None:
    events: list[str] = []
    session = _Session(_Result(row=_target_row()))
    uow = SimpleNamespace(
        session=session,
        commit=AsyncMock(side_effect=lambda: events.append("commit")),
    )

    async def query_info(host: str, port: int):
        assert events == ["commit"]
        events.append("info")
        assert (host, port) == ("query.example.com", 27016)
        return True, {"name": "test"}

    async def query_players(host: str, port: int):
        assert events == ["commit", "info"]
        events.append("players")
        assert (host, port) == ("query.example.com", 27016)
        return True, [{"name": "player"}]

    monkeypatch.setattr("services.a2s_query.a2s_service.query_server_info", query_info)
    monkeypatch.setattr("services.a2s_query.a2s_service.query_players", query_players)

    response = await monitoring.get_server_a2s_info(
        31,
        uow=uow,  # type: ignore[arg-type]
        current_user=_principal(),
    )

    assert response["success"] is True
    assert response["players"] == [{"name": "player"}]
    assert events == ["commit", "info", "players"]


@pytest.mark.asyncio
async def test_monitoring_missing_server_and_resources_fail_closed(monkeypatch) -> None:
    missing_uow = SimpleNamespace(
        session=_Session(_Result()),
        commit=AsyncMock(),
    )
    remote_query = AsyncMock()
    monkeypatch.setattr("services.a2s_query.a2s_service.query_server_info", remote_query)

    with pytest.raises(HTTPException) as missing_exc:
        await monitoring.get_server_a2s_info(
            404,
            uow=missing_uow,  # type: ignore[arg-type]
            current_user=_principal(),
        )
    assert missing_exc.value.status_code == 404
    remote_query.assert_not_awaited()

    active_uow = SimpleNamespace(
        session=_Session(_Result(row=_target_row())),
        commit=AsyncMock(),
    )
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    container=SimpleNamespace(redis=None),
                )
            ),
        }
    )
    with pytest.raises(HTTPException) as redis_exc:
        await monitoring.get_monitoring_logs(
            31,
            request,
            uow=active_uow,  # type: ignore[arg-type]
            current_user=_principal(),
        )
    assert redis_exc.value.status_code == 503
    active_uow.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_shared_uow_dependency_and_admin_policy_fail_closed() -> None:
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    container=SimpleNamespace(database=SimpleNamespace()),
                )
            ),
        }
    )
    dependency = get_unit_of_work(request)
    with pytest.raises(HTTPException) as database_exc:
        await anext(dependency)
    assert database_exc.value.status_code == 503

    with pytest.raises(HTTPException) as admin_exc:
        await monitoring.get_admin_principal(_principal())
    assert admin_exc.value.status_code == 403


@pytest.mark.asyncio
async def test_principal_authentication_never_falls_back_to_global_database() -> None:
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    container=SimpleNamespace(database=None),
                )
            ),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_principal(
            request,
            token=create_access_token({"sub": "7"}),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Authentication database is unavailable"
