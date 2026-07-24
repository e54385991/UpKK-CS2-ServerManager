from __future__ import annotations

import os

import pytest

from cs2_manager.infrastructure import DatabaseResource
from modules.config import settings
from services.redis_manager import RedisManager

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with MySQL 8 and Redis 7 available",
    ),
]


@pytest.mark.asyncio
async def test_mysql_and_redis_runtime_adapters_are_ready() -> None:
    database = DatabaseResource.from_settings(settings, initialize_schema=False)
    redis = RedisManager()
    try:
        assert await database.ping() is True
        assert await redis.ping() is True
    finally:
        await redis.close()
        await database.close()
