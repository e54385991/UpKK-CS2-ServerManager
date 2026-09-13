"""In-memory Redis stand-ins for panel-monitor history tests."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any


class FakeRedisClient:
    """Sorted-set + KV fake that can optionally raise ConnectionError."""

    def __init__(self, *, fail: bool = False, fail_keys: frozenset[str] | None = None) -> None:
        self.fail = fail
        self.fail_keys = fail_keys or frozenset()
        self.zsets: dict[str, list[tuple[Any, float]]] = {}
        self.kv: dict[str, Any] = {}

    def _raise_if_fail(self, key: str | None = None) -> None:
        if self.fail or (key is not None and key in self.fail_keys):
            raise ConnectionError("down")

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._raise_if_fail(key)
        items = self.zsets.setdefault(key, [])
        for member, score in mapping.items():
            items[:] = [(item, value) for item, value in items if item != member]
            items.append((str(member), float(score)))
        return len(mapping)

    async def zremrangebyscore(self, key: str, lo: float, hi: float) -> int:
        self._raise_if_fail(key)
        items = self.zsets.get(key, [])
        kept = [(member, score) for member, score in items if score < lo or score > hi]
        removed = len(items) - len(kept)
        self.zsets[key] = kept
        return removed

    async def zremrangebyrank(self, key: str, start: int, stop: int) -> int:
        self._raise_if_fail(key)
        items = self.zsets.get(key, [])
        items.sort(key=lambda pair: pair[1])
        length = len(items)
        if length == 0:
            return 0
        lo = start if start >= 0 else length + start
        hi = stop if stop >= 0 else length + stop
        if lo > hi:
            return 0
        lo = max(0, lo)
        hi = min(length - 1, hi)
        removed = hi - lo + 1
        del items[lo : hi + 1]
        self.zsets[key] = items
        return removed

    async def expire(self, key: str, _ttl: int) -> bool:
        self._raise_if_fail(key)
        return True

    async def zrangebyscore(
        self,
        key: str,
        lo: float,
        hi: float,
        withscores: bool = False,
    ) -> list[Any]:
        self._raise_if_fail(key)
        items = [(member, score) for member, score in self.zsets.get(key, []) if lo <= score <= hi]
        if withscores:
            return items
        return [member for member, _score in items]

    async def get(self, key: str) -> Any:
        self._raise_if_fail(key)
        return self.kv.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        self._raise_if_fail(key)
        self.kv[key] = value
        return True


def patch_history_redis(monkeypatch: Any, client: FakeRedisClient) -> FakeRedisClient:
    fake = SimpleNamespace(client=client, prefixed_key=lambda key: key)
    monkeypatch.setattr(sys.modules["services.panel_monitor.history"], "redis_manager", fake)
    return client
