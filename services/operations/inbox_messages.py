"""Bounded Redis reads for inbox latest-event text."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

REDIS_BATCH = 500
EVENT_TAIL_WINDOW = 16


def batched(items: Sequence[str], size: int = REDIS_BATCH) -> Iterable[list[str]]:
    sequence = list(items)
    for offset in range(0, len(sequence), size):
        yield sequence[offset : offset + size]


def parse_event(payload: object) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    raw: str
    if isinstance(payload, (bytes, bytearray)):
        raw = payload.decode("utf-8", "ignore")
    elif isinstance(payload, str):
        raw = payload
    else:
        return None
    if not raw:
        return None
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def event_text(payload: object) -> str | None:
    parsed = parse_event(payload)
    if parsed is None:
        return None
    message = str(parsed.get("message") or "").strip()
    return message or None


def last_event_text(events: Sequence[object] | None) -> str | None:
    if not events:
        return None
    return event_text(events[-1])


def first_valid_text(values: Sequence[object] | None) -> str | None:
    if not values:
        return None
    for item in reversed(values):
        message = event_text(item)
        if message:
            return message
    return None


def record_message(record: dict[str, Any] | None) -> str | None:
    if record is None:
        return None
    message = str(record.get("message") or "").strip()
    return message or None


async def get_many_chunked(
    redis: Any, keys: Sequence[str], *, batch: int = REDIS_BATCH
) -> list[Any]:
    if not keys:
        return []
    getter = getattr(redis, "get_many", None)
    if getter is None:
        return [await redis.get(key) for key in keys]
    values: list[Any] = []
    for chunk in batched(keys, batch):
        values.extend(await getter(chunk))
    return values


async def list_ranges(
    redis: Any,
    keys: Sequence[str],
    start: int,
    end: int,
    *,
    batch: int = REDIS_BATCH,
) -> list[list[Any]]:
    if not keys:
        return []
    collected: list[list[Any]] = []
    for chunk in batched(keys, batch):
        collected.extend(await _execute_lrange(redis, chunk, start, end))
    return collected


async def _execute_lrange(redis: Any, keys: list[str], start: int, end: int) -> list[list[Any]]:
    empty = [[] for _ in keys]
    client = getattr(redis, "client", None)
    prefix = getattr(redis, "prefixed_key", lambda key: key)
    if client is None or not hasattr(client, "pipeline"):
        return empty
    try:
        pipeline = client.pipeline(transaction=False)
        for key in keys:
            pipeline.lrange(prefix(key), start, end)
        raw = await pipeline.execute()
    except Exception:
        return empty
    if not isinstance(raw, list):
        return empty
    return [item if isinstance(item, list) else [] for item in raw]


async def read_latest_messages(redis: Any, event_keys: dict[str, str]) -> dict[str, str]:
    """Happy path: list tails only. Corrupt tails use a bounded reverse window."""
    if not event_keys:
        return {}
    operation_ids = list(event_keys)
    keys = [event_keys[operation_id] for operation_id in operation_ids]
    found: dict[str, str] = {}
    tails = await list_ranges(redis, keys, -1, -1)
    missing: list[str] = []
    for operation_id, values in zip(operation_ids, tails, strict=True):
        text = first_valid_text(values)
        if text:
            found[operation_id] = text
        else:
            missing.append(operation_id)
    if not missing:
        return found
    window_keys = [event_keys[operation_id] for operation_id in missing]
    windows = await list_ranges(redis, window_keys, -EVENT_TAIL_WINDOW, -1)
    for operation_id, values in zip(missing, windows, strict=True):
        text = first_valid_text(values)
        if text:
            found[operation_id] = text
    return found
