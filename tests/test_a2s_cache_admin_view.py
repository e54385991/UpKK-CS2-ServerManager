"""A2S cache visibility regressions for the server admin view."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import public
from modules import database
from modules.models import Server
from services.a2s_cache_service import a2s_cache_service


@pytest.mark.asyncio
async def test_a2s_admin_view_rejects_non_admin_users():
    user = SimpleNamespace(id=7, is_admin=False)
    request = SimpleNamespace(query_params={"admin_view": "true"})

    with pytest.raises(HTTPException) as caught:
        await public.get_user_servers_a2s_cache(request=request, current_user=user)

    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_a2s_admin_view_returns_cache_for_all_servers(monkeypatch):
    session = object()

    @asynccontextmanager
    async def fake_session_maker():
        yield session

    get_all = AsyncMock(return_value=[SimpleNamespace(id=11), SimpleNamespace(id=22)])
    get_all_by_user = AsyncMock()
    get_cached_info = AsyncMock(side_effect=[{"success": True}, {"success": False}])

    monkeypatch.setattr(database, "async_session_maker", fake_session_maker)
    monkeypatch.setattr(Server, "get_all", get_all)
    monkeypatch.setattr(Server, "get_all_by_user", get_all_by_user)
    monkeypatch.setattr(a2s_cache_service, "get_cached_info", get_cached_info)
    monkeypatch.setattr(
        a2s_cache_service,
        "get_latest_steam_version",
        AsyncMock(return_value=None),
    )

    response = await public.get_user_servers_a2s_cache(
        request=SimpleNamespace(query_params={"admin_view": "true"}),
        current_user=SimpleNamespace(id=1, is_admin=True),
    )

    get_all.assert_awaited_once_with(session)
    get_all_by_user.assert_not_awaited()
    assert response["servers"] == {
        "11": {"success": True},
        "22": {"success": False},
    }
    assert response["debug"]["admin_view"] is True
