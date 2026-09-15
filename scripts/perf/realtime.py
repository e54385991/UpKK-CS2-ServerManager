"""Thirty inbox SSE sessions plus a progress / complete / fail / clear script."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic, perf_counter
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from scripts.perf.profiles import FLEETS
from scripts.perf.report import percentile_block

INBOX_EVENTS = "/api/v1/operations/inbox/events"
INBOX_FAILED = "/api/v1/operations/inbox/failed"
EVENT_CYCLE = ("progress", "complete", "fail", "clear")


class InboxLifecycle(Protocol):
    async def play(self, step: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    name: str
    bytes: int


def parse_sse_chunk(payload: str) -> list[ParsedEvent]:
    events: list[ParsedEvent] = []
    current = "message"
    body: list[str] = []
    for raw in payload.splitlines():
        line = raw.rstrip("\r")
        if line == "":
            if body or current != "message":
                encoded = "\n".join(body)
                events.append(ParsedEvent(current, len(encoded.encode("utf-8"))))
            current = "message"
            body = []
            continue
        if line.startswith(":"):
            events.append(ParsedEvent("keep-alive", len(line.encode("utf-8"))))
            continue
        if line.startswith("event:"):
            current = line.split(":", 1)[1].strip() or "message"
            continue
        if line.startswith("data:"):
            body.append(line.split(":", 1)[1].lstrip())
    return events


class ScriptedLifecycle:
    """Deterministic injector used by unit tests and the isolated driver."""

    def __init__(self) -> None:
        self.played: list[str] = []

    async def play(self, step: str) -> str:
        self.played.append(step)
        return step


def next_cycle_step(index: int) -> str:
    return EVENT_CYCLE[index % len(EVENT_CYCLE)]


async def next_sse_chunk(
    stream: Any,
    stop_at: float,
    *,
    now: Callable[[], float] | None = None,
    waiter: Any = None,
) -> str | None:
    """Return the next chunk, or None when the window ends or the stream does."""
    clock = now or monotonic
    wait = waiter or asyncio.wait_for
    while clock() < stop_at:
        remaining = stop_at - clock()
        try:
            chunk = await wait(stream.__anext__(), timeout=min(1.0, remaining))
        except TimeoutError:
            continue
        except StopAsyncIteration:
            return None
        if isinstance(chunk, str):
            return chunk
        return None
    return None


async def drain_sse(
    response: httpx.Response,
    stop_at: float,
    latencies: list[float],
    names: dict[str, int],
    bytes_total: list[int],
) -> None:
    first = True
    started = perf_counter()
    stream = response.aiter_text()
    while True:
        chunk = await next_sse_chunk(stream, stop_at)
        if chunk is None:
            return
        for event in parse_sse_chunk(chunk):
            names[event.name] = names.get(event.name, 0) + 1
            bytes_total[0] += event.bytes
            if first and event.name == "inbox":
                latencies.append((perf_counter() - started) * 1000)
                first = False


async def play_lifecycle(injector: InboxLifecycle, stop_at: float) -> list[str]:
    """Drive the script without blocking past the measure window."""
    played: list[str] = []
    step = 0
    while monotonic() < stop_at:
        name = next_cycle_step(step)
        remaining = stop_at - monotonic()
        if remaining <= 0:
            break
        try:
            played.append(await asyncio.wait_for(injector.play(name), timeout=min(1.0, remaining)))
        except TimeoutError:
            played.append(f"{name}:timeout")
        step += 1
        remaining = stop_at - monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.25, remaining))
    return played


async def open_inbox_sessions(
    *,
    client: httpx.AsyncClient,
    token: str,
    sessions: int,
    seconds: int,
    injector: InboxLifecycle | None = None,
) -> dict[str, Any]:
    """Hold ``sessions`` EventSource-style GETs while the script mutates state."""
    stop_at = monotonic() + seconds
    latencies: list[float] = []
    names: dict[str, int] = {}
    bytes_total = [0]
    errors = 0
    headers = {"authorization": f"Bearer {token}", "accept": "text/event-stream"}

    async def one_session() -> None:
        nonlocal errors
        try:
            async with client.stream("GET", INBOX_EVENTS, headers=headers) as response:
                if response.status_code >= 400:
                    errors += 1
                    return
                await drain_sse(response, stop_at, latencies, names, bytes_total)
        except httpx.HTTPError:
            errors += 1

    readers = [asyncio.create_task(one_session()) for _ in range(sessions)]
    played = await play_lifecycle(injector, stop_at) if injector is not None else []
    try:
        await asyncio.wait_for(asyncio.gather(*readers), timeout=2.0)
    except TimeoutError:
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
    return {
        "sessions": sessions,
        "seconds": seconds,
        "errors": errors,
        "events": dict(names),
        "bytes": bytes_total[0],
        "first_inbox_ms": percentile_block(latencies),
        "injected": played,
        "target_sessions": FLEETS["fleet-500"].online_users,
    }


async def run_realtime_rounds(
    *,
    token: str,
    sessions: int,
    seconds: int,
    transport: httpx.AsyncBaseTransport | None,
    base_url: str,
    injector: InboxLifecycle | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(transport=transport, base_url=base_url, timeout=30.0) as client:
        measured = await open_inbox_sessions(
            client=client,
            token=token,
            sessions=sessions,
            seconds=seconds,
            injector=injector,
        )
    return measured
