"""Capacity and round-trip tests for deployment progress storage."""

from __future__ import annotations

import json

import pytest

from services.redis_manager import RedisManager


class _Pipeline:
    def __init__(self) -> None:
        self.commands: list[tuple] = []
        self.executions = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def rpush(self, *args):
        self.commands.append(("rpush", *args))
        return self

    def ltrim(self, *args):
        self.commands.append(("ltrim", *args))
        return self

    def expire(self, *args):
        self.commands.append(("expire", *args))
        return self

    async def execute(self):
        self.executions += 1


class _Redis:
    def __init__(self) -> None:
        self.pipeline_instance = _Pipeline()
        self.pipeline_transaction = None
        self.lrange_args = None

    def pipeline(self, *, transaction):
        self.pipeline_transaction = transaction
        return self.pipeline_instance

    async def lrange(self, *args):
        self.lrange_args = args
        return [json.dumps({"type": "output", "message": "tail", "timestamp": "now"})]


@pytest.mark.asyncio
async def test_deployment_progress_append_is_batched_and_bounded():
    manager = RedisManager()
    manager.client = _Redis()

    assert await manager.append_deployment_progress(7, "output", "line", "now") is True

    key = "deployment_progress:7"
    pipeline = manager.client.pipeline_instance
    assert manager.client.pipeline_transaction is False
    assert pipeline.executions == 1
    assert [command[0] for command in pipeline.commands] == ["rpush", "ltrim", "expire"]
    assert pipeline.commands[1] == (
        "ltrim",
        key,
        -manager.MAX_DEPLOYMENT_PROGRESS_ENTRIES,
        -1,
    )
    assert pipeline.commands[2] == ("expire", key, 7200)


@pytest.mark.asyncio
async def test_deployment_progress_bounds_each_message_by_utf8_bytes():
    manager = RedisManager()
    manager.client = _Redis()
    oversized = "长" * manager.MAX_DEPLOYMENT_PROGRESS_MESSAGE_BYTES

    assert await manager.append_deployment_progress(7, "output", oversized, "now") is True

    stored = json.loads(manager.client.pipeline_instance.commands[0][2])
    assert len(stored["message"].encode("utf-8")) <= manager.MAX_DEPLOYMENT_PROGRESS_MESSAGE_BYTES
    assert stored["message"].startswith("[... earlier output truncated ...]")
    assert stored["message"].endswith("长")


@pytest.mark.asyncio
async def test_deployment_progress_replay_reads_only_the_bounded_tail():
    manager = RedisManager()
    manager.client = _Redis()

    progress = await manager.get_deployment_progress(7)

    assert manager.client.lrange_args == (
        "deployment_progress:7",
        -manager.MAX_DEPLOYMENT_PROGRESS_ENTRIES,
        -1,
    )
    assert progress == [{"type": "output", "message": "tail", "timestamp": "now"}]
