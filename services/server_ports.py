"""Allocate CS2 game ports that do not collide with other panel servers on a host."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import Server

DEFAULT_GAME_PORT = 27015
GAME_PORT_STRIDE = 10


class GamePortUnavailableError(ValueError):
    """No free game port remains in the supported range on this host."""


class HostPortRecord(Protocol):
    host: str
    game_port: int


def occupied_game_ports(servers: Sequence[HostPortRecord], host: str) -> set[int]:
    """Collect game, client, and enabled GOTV ports used on ``host``."""

    occupied: set[int] = set()
    for server in servers:
        if server.host != host:
            continue
        if isinstance(server.game_port, int):
            occupied.add(server.game_port)
            client_port = getattr(server, "client_port", None) or server.game_port + 1
            if 1 <= client_port <= 65535:
                occupied.add(client_port)
        tv_port = getattr(server, "tv_port", None)
        if getattr(server, "tv_enable", False) and isinstance(tv_port, int):
            occupied.add(tv_port)
    return occupied


def game_port_available(candidate: int, occupied: set[int]) -> bool:
    """True when ``candidate`` and the default client port (candidate + 1) are free."""

    if not 1 <= candidate <= 65534:
        return False
    return candidate not in occupied and candidate + 1 not in occupied


def next_available_game_port(start: int, occupied: set[int]) -> int:
    """Return the next free game port, preferring ``start`` then steps of 10."""

    candidate = start
    while candidate <= 65534:
        if game_port_available(candidate, occupied):
            return candidate
        candidate += GAME_PORT_STRIDE

    candidate = DEFAULT_GAME_PORT
    while candidate <= 65534:
        if game_port_available(candidate, occupied):
            return candidate
        candidate += 1
    raise GamePortUnavailableError("No available game port remains on this host")


def suggested_game_port(occupied: set[int]) -> int:
    """Default one-click port: 27015, or +10 from that stride when it is taken."""

    try:
        return next_available_game_port(DEFAULT_GAME_PORT, occupied)
    except GamePortUnavailableError:
        return DEFAULT_GAME_PORT


async def list_servers_on_hosts(db: AsyncSession, hosts: set[str]) -> list[Server]:
    if not hosts:
        return []
    result = await db.execute(select(Server).where(col(Server.host).in_(hosts)))
    return list(result.scalars().all())


async def list_servers_on_host(db: AsyncSession, host: str) -> list[Server]:
    return await list_servers_on_hosts(db, {host})


async def suggested_game_ports_by_host(db: AsyncSession, hosts: set[str]) -> dict[str, int]:
    servers = await list_servers_on_hosts(db, hosts)
    grouped: dict[str, list[Server]] = {host: [] for host in hosts}
    for server in servers:
        grouped.setdefault(server.host, []).append(server)
    return {
        host: suggested_game_port(occupied_game_ports(rows, host)) for host, rows in grouped.items()
    }


async def allocate_game_port(
    db: AsyncSession,
    host: str,
    requested: int = DEFAULT_GAME_PORT,
) -> int:
    occupied = occupied_game_ports(await list_servers_on_host(db, host), host)
    return next_available_game_port(requested, occupied)
