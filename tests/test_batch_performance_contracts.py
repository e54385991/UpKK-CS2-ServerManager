"""Deterministic I/O budgets for 40-server batch initialization."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.redis_manager import RedisManager
from services.servers.batch import authorized_server_ids


class ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


@pytest.mark.asyncio
async def test_forty_server_authorization_uses_one_sql_query():
    db = SimpleNamespace(execute_calls=0)

    async def execute(_statement):
        db.execute_calls += 1
        return ScalarResult(range(1, 41))

    db.execute = execute
    assert await authorized_server_ids(db, list(range(1, 41)), 7) == list(range(1, 41))
    assert db.execute_calls == 1


class Pipeline:
    def __init__(self):
        self.commands = []
        self.execute_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def setex(self, key, expire, value):
        self.commands.append((key, expire, value))

    async def execute(self):
        self.execute_calls += 1


@pytest.mark.asyncio
async def test_forty_pending_statuses_use_one_redis_pipeline():
    pipeline = Pipeline()
    redis = object.__new__(RedisManager)
    redis.client = SimpleNamespace(pipeline=lambda **_kwargs: pipeline)

    assert await redis.set_batch_action_statuses(
        "batch",
        list(range(1, 41)),
        "pending",
        "Queued",
    )
    assert len(pipeline.commands) == 40
    assert pipeline.execute_calls == 1
    assert all(json.loads(command[2])["status"] == "pending" for command in pipeline.commands)
