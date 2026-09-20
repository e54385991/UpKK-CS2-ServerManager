from types import SimpleNamespace

import pytest

from services.server_ports import (
    DEFAULT_GAME_PORT,
    GAME_PORT_STRIDE,
    GamePortUnavailableError,
    allocate_game_port,
    game_port_available,
    next_available_game_port,
    occupied_game_ports,
    suggested_game_port,
    suggested_game_ports_by_host,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _statement):
        return _Result(self.rows)


def _server(**overrides):
    values = {
        "host": "10.0.0.8",
        "game_port": 27015,
        "client_port": None,
        "tv_enable": False,
        "tv_port": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_next_available_keeps_default_when_host_is_empty():
    assert next_available_game_port(DEFAULT_GAME_PORT, set()) == 27015
    assert suggested_game_port(set()) == 27015


def test_next_available_steps_ten_from_occupied_game_and_client_ports():
    occupied = occupied_game_ports([_server()], "10.0.0.8")
    assert occupied == {27015, 27016}
    assert next_available_game_port(DEFAULT_GAME_PORT, occupied) == 27025
    assert suggested_game_port(occupied) == 27025


def test_next_available_skips_enabled_tv_port_on_the_same_stride():
    occupied = occupied_game_ports(
        [
            _server(),
            _server(game_port=27025, client_port=27036, tv_enable=True, tv_port=27046),
        ],
        "10.0.0.8",
    )
    assert next_available_game_port(DEFAULT_GAME_PORT, occupied) == 27055


def test_game_port_available_rejects_client_port_collision():
    assert game_port_available(27025, {27026}) is False
    assert game_port_available(27025, {27024}) is True


def test_occupied_ports_are_scoped_to_the_requested_host():
    occupied = occupied_game_ports(
        [_server(), _server(host="10.0.0.9", game_port=27025)],
        "10.0.0.8",
    )
    assert occupied == {27015, 27016}


def test_next_available_raises_when_the_range_is_exhausted():
    occupied = set(range(1, 65536))
    with pytest.raises(GamePortUnavailableError, match="No available game port"):
        next_available_game_port(DEFAULT_GAME_PORT, occupied)
    assert suggested_game_port(occupied) == DEFAULT_GAME_PORT


@pytest.mark.asyncio
async def test_allocate_game_port_uses_requested_port_when_free():
    port = await allocate_game_port(_Db([_server(game_port=27025)]), "10.0.0.8", 27015)
    assert port == 27015


@pytest.mark.asyncio
async def test_allocate_game_port_advances_by_stride_when_requested_is_taken():
    port = await allocate_game_port(_Db([_server()]), "10.0.0.8", DEFAULT_GAME_PORT)
    assert port == DEFAULT_GAME_PORT + GAME_PORT_STRIDE


@pytest.mark.asyncio
async def test_suggested_game_ports_by_host_maps_each_address():
    mapping = await suggested_game_ports_by_host(
        _Db([_server(), _server(host="10.0.0.9", game_port=27035)]),
        {"10.0.0.8", "10.0.0.9", "10.0.0.10"},
    )
    assert mapping["10.0.0.8"] == 27025
    assert mapping["10.0.0.9"] == 27015
    assert mapping["10.0.0.10"] == 27015
