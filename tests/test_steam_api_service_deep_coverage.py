"""覆盖 Steam API 版本查询和 GSLT 创建的响应边界。"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from services import steam_api_service as module


def test_steam_version_helpers_cover_numeric_and_datetime_edges(monkeypatch):
    assert module.steam_version_query(None) == "1"
    assert module.steam_version_query(" 1.41.2.5 ") == "14125"
    assert module.steam_version_query("release") == "1"
    assert (
        module.advertised_version_from_response({"message": "Server version required: 1.2.3"})
        == "1.2.3"
    )
    assert module.advertised_version_from_response({"required_version": 14125}) == "14125"
    assert module.advertised_version_from_response({"required_version": ""}) is None
    assert module.resolve_advertised_version(None, installed="1.2.3", up_to_date=True) == "1.2.3"
    assert module.resolve_advertised_version(None, installed="1.2.3", up_to_date=False) is None
    assert module.resolve_advertised_version("123", installed="1.2.3", up_to_date=False) == "1.2.3"
    assert module.resolve_advertised_version("old", installed="1.2.3", up_to_date=False) == "old"
    assert module.versions_equivalent("1.41.2.5", "14125") is True
    assert module.versions_equivalent(None, "1") is False
    assert module._looks_like_egress_failure("certificate timeout") is True
    assert module._looks_like_egress_failure("bad payload") is False
    assert module.SteamAPIService.parse_version_from_a2s(None) is None
    assert module.SteamAPIService.parse_version_from_a2s("1.2.3/123") == "1.2.3"
    assert module.SteamAPIService.parse_version_from_a2s("  ") is None
    assert module.SteamAPIService.should_check_version(None) is True
    now = module.get_current_time()
    monkeypatch.setattr(module, "get_current_time", lambda: now)
    assert module.SteamAPIService.should_check_version(now - timedelta(hours=2), 1) is True
    assert module.SteamAPIService.should_check_version(now - timedelta(minutes=1), 1) is False


@pytest.mark.asyncio
async def test_check_version_cache_http_and_response_shapes(monkeypatch):
    service = module.SteamAPIService()
    monkeypatch.setattr(
        module.redis_manager,
        "get",
        AsyncMock(return_value={"required_version": "1.2.3", "message": "cached"}),
    )
    ok, cached = await service.check_version("123", use_cache=True)
    assert ok and cached["cached"] is True and cached["up_to_date"] is True

    monkeypatch.setattr(module.redis_manager, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(
        module.http_helper, "get", AsyncMock(return_value=(False, None, "network timeout"))
    )
    ok, result = await service.check_version(use_cache=True)
    assert not ok and "Cannot reach" in result["error"]
    monkeypatch.setattr(
        module.http_helper, "get", AsyncMock(return_value=(False, None, "bad request"))
    )
    ok, result = await service.check_version(use_cache=False)
    assert not ok and result["error"] == "bad request"

    for payload in (None, {"other": 1}, {"response": []}):
        monkeypatch.setattr(module.http_helper, "get", AsyncMock(return_value=(True, payload, "")))
        ok, result = await service.check_version(use_cache=False)
        assert not ok and result["error"]

    response = {"response": {"up_to_date": True, "message": "", "required_version": None}}
    monkeypatch.setattr(module.http_helper, "get", AsyncMock(return_value=(True, response, "")))
    monkeypatch.setattr(module.redis_manager, "set", AsyncMock())
    ok, result = await service.check_version("1.2.3", use_cache=False)
    assert ok and result["up_to_date"] is True and result["required_version"] == "1.2.3"

    response = {"response": {"up_to_date": False, "message": "Server version required: 1.4.5"}}
    monkeypatch.setattr(module.http_helper, "get", AsyncMock(return_value=(True, response, "")))
    ok, result = await service.check_version("1.2.3", use_cache=False)
    assert ok and result["required_version"] == "1.4.5"
    module.redis_manager.set.assert_awaited()

    monkeypatch.setattr(
        module.http_helper, "get", AsyncMock(side_effect=RuntimeError("proxy down"))
    )
    ok, result = await service.check_version(use_cache=False)
    assert not ok and "Cannot reach" in result["error"]


@pytest.mark.asyncio
async def test_create_game_server_account_success_and_failures(monkeypatch):
    service = module.SteamAPIService()
    post = AsyncMock(
        return_value=(True, {"response": {"login_token": "token", "steamid": "id"}}, "")
    )
    monkeypatch.setattr(module.http_helper, "post", post)
    ok, result = await service.create_game_server_account("key", "")
    assert ok and result["login_token"] == "token"
    assert post.await_args.kwargs["params"]["memo"] == "CS2 Server"

    cases = [
        (False, "", "request failed"),
        (True, None, ""),
        (True, {"response": []}, ""),
        (True, {"response": {"error": "quota"}}, ""),
    ]
    for success, payload, error in cases:
        monkeypatch.setattr(
            module.http_helper, "post", AsyncMock(return_value=(success, payload, error))
        )
        ok, result = await service.create_game_server_account("key")
        assert not ok and result["error"]

    monkeypatch.setattr(module.http_helper, "post", AsyncMock(side_effect=RuntimeError("offline")))
    ok, result = await service.create_game_server_account("key")
    assert not ok and "offline" in result["error"]
