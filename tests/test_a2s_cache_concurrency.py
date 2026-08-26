"""Bounded fan-out tests for background A2S collection."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from services.a2s_cache_service import (
    MAX_CONCURRENT_A2S_QUERIES,
    A2SCacheService,
)


@pytest.mark.asyncio
async def test_a2s_collection_queries_servers_with_bounded_fanout(monkeypatch):
    servers = [
        SimpleNamespace(
            id=server_id,
            should_skip_background_checks=lambda: False,
        )
        for server_id in range(MAX_CONCURRENT_A2S_QUERIES + 3)
    ]

    class Scalars:
        @staticmethod
        def all():
            return servers

    class Result:
        @staticmethod
        def scalars():
            return Scalars()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement):
            return Result()

    monkeypatch.setattr("modules.database.async_session_maker", Session)

    service = A2SCacheService()
    active = 0
    maximum_active = 0
    queried: set[int] = set()

    async def query(server):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        queried.add(server.id)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(service, "_query_and_cache_server", query)

    await service._query_all_servers()

    assert queried == {server.id for server in servers}
    assert maximum_active == MAX_CONCURRENT_A2S_QUERIES


@pytest.mark.asyncio
async def test_a2s_collection_cancels_unfinished_queries_at_scan_deadline(monkeypatch):
    servers = [
        SimpleNamespace(
            id=server_id,
            should_skip_background_checks=lambda: False,
        )
        for server_id in range(100)
    ]

    class Scalars:
        @staticmethod
        def all():
            return servers

    class Result:
        @staticmethod
        def scalars():
            return Scalars()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement):
            return Result()

    monkeypatch.setattr("modules.database.async_session_maker", Session)

    service = A2SCacheService()
    active = 0
    maximum_active = 0

    async def query(_server):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            await asyncio.sleep(1)
        finally:
            active -= 1

    monkeypatch.setattr(service, "_query_and_cache_server", query)

    started = time.perf_counter()
    await service._query_all_servers(timeout=0.05)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert maximum_active <= MAX_CONCURRENT_A2S_QUERIES
    assert active == 0
