"""Unit coverage for steam.inf parsing and Steam advertised-version compare."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.game_version import inspect_game_version, versions_match
from services.steam_api_service import steam_api_service, steam_version_query
from services.steam_inf_service import parse_steam_inf_fields


def test_parse_steam_inf_fields_reads_patch_and_server_build():
    version, build = parse_steam_inf_fields(
        "PatchVersion=1.41.2.5\nClientVersion=14125\nServerVersion=14125\n"
    )
    assert version == "1.41.2.5"
    assert build == "14125"


def test_parse_steam_inf_fields_falls_back_to_client_version():
    version, build = parse_steam_inf_fields("PatchVersion=1.40.0.1\nClientVersion=14001\n")
    assert version == "1.40.0.1"
    assert build == "14001"


def test_steam_version_query_uses_numeric_client_version():
    assert steam_version_query("1.41.2.5") == "14125"
    assert steam_version_query(None) == "1"


def test_versions_match_dotted_and_numeric():
    assert versions_match("1.41.2.5", "14125")
    assert versions_match("1.41.2.5", "1.41.2.5")
    assert not versions_match("1.41.2.4", "1.41.2.5")
    assert not versions_match(None, "1.41.2.5")


@pytest.mark.asyncio
async def test_inspect_game_version_prefers_steam_inf(monkeypatch):
    server = SimpleNamespace(id=2, current_game_version="1.40.0.0")
    monkeypatch.setattr(
        "services.game_version.steam_inf_service.get_steam_inf_details",
        AsyncMock(return_value=(True, "1.41.2.5", "14125")),
    )
    monkeypatch.setattr(
        "services.game_version.steam_api_service.check_version",
        AsyncMock(
            return_value=(
                True,
                {
                    "up_to_date": False,
                    "required_version": "1.41.2.6",
                    "message": "Server version required: 1.41.2.6",
                },
            )
        ),
    )
    snapshot = await inspect_game_version(server)
    assert snapshot.installed_version == "1.41.2.5"
    assert snapshot.installed_build_id == "14125"
    assert snapshot.installed_source == "steam.inf"
    assert snapshot.advertised_version == "1.41.2.6"
    assert snapshot.up_to_date is False
    assert snapshot.steam_check_ok is True


@pytest.mark.asyncio
async def test_inspect_game_version_falls_back_to_database(monkeypatch):
    server = SimpleNamespace(id=2, current_game_version="1.41.2.5")
    monkeypatch.setattr(
        "services.game_version.steam_inf_service.get_steam_inf_details",
        AsyncMock(return_value=(False, None, None)),
    )
    monkeypatch.setattr(
        "services.game_version.steam_api_service.check_version",
        AsyncMock(return_value=(True, {"up_to_date": True, "required_version": "1.41.2.5"})),
    )
    snapshot = await inspect_game_version(server)
    assert snapshot.installed_source == "database"
    assert snapshot.installed_version == "1.41.2.5"
    assert snapshot.up_to_date is True


@pytest.mark.asyncio
async def test_inspect_game_version_steam_failure(monkeypatch):
    server = SimpleNamespace(id=2, current_game_version=None)
    monkeypatch.setattr(
        "services.game_version.steam_inf_service.get_steam_inf_details",
        AsyncMock(return_value=(False, None, None)),
    )
    monkeypatch.setattr(
        "services.game_version.steam_api_service.check_version",
        AsyncMock(return_value=(False, {"error": "timeout"})),
    )
    snapshot = await inspect_game_version(server)
    assert snapshot.installed_source == "unknown"
    assert snapshot.steam_check_ok is False
    assert snapshot.up_to_date is None
    assert snapshot.steam_error == "timeout"


@pytest.mark.asyncio
async def test_inspect_game_version_swallows_exceptions(monkeypatch):
    server = SimpleNamespace(id=2, current_game_version="1.40.0.0")
    monkeypatch.setattr(
        "services.game_version.steam_inf_service.get_steam_inf_details",
        AsyncMock(side_effect=RuntimeError("ssh factory missing")),
    )
    snapshot = await inspect_game_version(server)
    assert snapshot.steam_check_ok is False
    assert snapshot.installed_version == "1.40.0.0"
    assert snapshot.installed_source == "database"


@pytest.mark.asyncio
async def test_check_version_uses_redis_cache(monkeypatch):
    monkeypatch.setattr(
        "services.steam_api_service.redis_manager.get",
        AsyncMock(
            return_value={
                "required_version": "1.41.7.8",
                "message": "Server version required: 1.41.7.8",
            }
        ),
    )
    http = AsyncMock()
    monkeypatch.setattr("services.steam_api_service.http_helper.get", http)
    ok, result = await steam_api_service.check_version("1.41.7.8")
    assert ok is True
    assert result["required_version"] == "1.41.7.8"
    assert result["up_to_date"] is True
    assert result["cached"] is True
    http.assert_not_called()
