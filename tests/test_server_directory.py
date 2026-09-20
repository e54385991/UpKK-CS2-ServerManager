"""Normalize and match game directories across panel server records."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.server_directory import (
    HOST_DIRECTORY_EXISTS_CODE,
    find_host_directory_server,
    host_directory_conflict_payload,
    normalize_game_directory,
)


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return _Scalars(self.rows)


class _DB:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _statement):
        return _Result(self.rows)


def test_normalize_game_directory_rejects_relative_and_root_paths():
    assert normalize_game_directory(" /home/steam/cs2/../cs2-2/") == "/home/steam/cs2-2"
    with pytest.raises(ValueError, match="absolute"):
        normalize_game_directory("home/steam/cs2")
    with pytest.raises(ValueError, match="root"):
        normalize_game_directory("/")
    with pytest.raises(ValueError, match="root"):
        normalize_game_directory("//")


@pytest.mark.asyncio
async def test_find_host_directory_server_matches_normalized_paths():
    occupied = SimpleNamespace(
        id=9,
        name="alpha",
        host="192.168.50.143",
        game_directory="/home/cs2server/cs2/",
        user_id=3,
    )
    other_host = SimpleNamespace(
        id=10,
        name="beta",
        host="192.168.50.143",
        game_directory="/home/cs2server/cs2-2",
        user_id=3,
    )
    db = _DB([occupied, other_host])
    found = await find_host_directory_server(db, "192.168.50.143", "/home/cs2server/cs2", 3)
    assert found is occupied
    missing = await find_host_directory_server(db, "192.168.50.143", "/home/cs2server/cs2-3", 3)
    assert missing is None


def test_host_directory_conflict_payload_includes_existing_server():
    server = SimpleNamespace(id=12, name="lan-1")
    payload = host_directory_conflict_payload(server, "192.168.50.143", "/home/cs2server/cs2")
    assert payload["code"] == HOST_DIRECTORY_EXISTS_CODE
    assert payload["existing_server_id"] == 12
    assert payload["existing_server_name"] == "lan-1"
    assert "lan-1" in payload["message"]
