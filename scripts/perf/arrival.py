"""Open-loop arrival scheduling so a slow route cannot starve the mix."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Callable, Mapping, Sequence

from scripts.perf.profiles import ARRIVAL_RATIO
from scripts.perf.report import percentile_block

Emit = Callable[[str, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ArrivalStats:
    scheduled: int
    emitted: int
    missed: int
    late: int
    wait_ms: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        waits = list(self.wait_ms)
        return {
            "scheduled": self.scheduled,
            "emitted": self.emitted,
            "missed": self.missed,
            "late": self.late,
            "wait_ms": percentile_block(waits),
        }


def scale_rates(
    rates: Mapping[str, float], ratio: float = ARRIVAL_RATIO
) -> dict[str, float]:
    """Keep the discovered mix; candidates reuse these scaled targets."""
    return {name: max(0.0, float(rate) * ratio) for name, rate in rates.items()}


def error_free_rates(routes: Mapping[str, Mapping[str, Any]]) -> dict[str, float] | None:
    """Reject a discovery window that recorded any HTTP error."""
    targets: dict[str, float] = {}
    for name, row in routes.items():
        if int(row.get("errors") or 0) > 0:
            return None
        targets[name] = float(row.get("throughput_rps") or 0.0)
    return targets


def arrival_offsets(target_rps: float, duration_s: float) -> list[float]:
    """Evenly spaced due times in ``[0, duration)``."""
    if target_rps <= 0 or duration_s <= 0:
        return []
    interval = 1.0 / target_rps
    count = int(target_rps * duration_s)
    return [index * interval for index in range(count)]


def merge_arrival_stats(rows: Sequence[ArrivalStats]) -> ArrivalStats:
    waits: list[float] = []
    scheduled = emitted = missed = late = 0
    for row in rows:
        scheduled += row.scheduled
        emitted += row.emitted
        missed += row.missed
        late += row.late
        waits.extend(row.wait_ms)
    return ArrivalStats(scheduled, emitted, missed, late, tuple(waits))


def load_arrival_targets(payload: Mapping[str, Any]) -> dict[str, float] | None:
    block = payload.get("arrival")
    if not isinstance(block, Mapping):
        return None
    targets = block.get("targets")
    if not isinstance(targets, Mapping) or not targets:
        return None
    return {str(name): float(rate) for name, rate in targets.items()}


async def run_paced_route(
    *,
    name: str,
    path: str,
    target_rps: float,
    duration_s: float,
    emit: Emit,
    now: Callable[[], float] | None = None,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> ArrivalStats:
    """Emit one route on a fixed cadence. Late slots still try to fire."""
    import asyncio

    clock = now or monotonic
    pause = sleeper or asyncio.sleep
    offsets = arrival_offsets(target_rps, duration_s)
    start = clock()
    stop_at = start + duration_s
    emitted = 0
    missed = 0
    late = 0
    waits: list[float] = []
    for index, offset in enumerate(offsets):
        due = start + offset
        current = clock()
        if current >= stop_at:
            missed += len(offsets) - index
            break
        if current <= due:
            if current < due:
                await pause(due - current)
            waits.append(0.0)
        else:
            waits.append((current - due) * 1000)
            late += 1
        if clock() >= stop_at:
            missed += 1
            continue
        await emit(name, path)
        emitted += 1
    return ArrivalStats(len(offsets), emitted, missed, late, tuple(waits))


def _as_arrival_stats(row: ArrivalStats | Mapping[str, Any]) -> ArrivalStats:
    if isinstance(row, ArrivalStats):
        return row
    wait = row.get("wait_ms")
    waits: tuple[float, ...]
    if isinstance(wait, Mapping):
        waits = ()
    elif isinstance(wait, (list, tuple)):
        waits = tuple(float(item) for item in wait)
    else:
        waits = ()
    return ArrivalStats(
        scheduled=int(row.get("scheduled") or 0),
        emitted=int(row.get("emitted") or 0),
        missed=int(row.get("missed") or 0),
        late=int(row.get("late") or 0),
        wait_ms=waits,
    )


def arrival_report(
    *,
    discovered: Mapping[str, float] | None,
    targets: Mapping[str, float],
    stats: Mapping[str, ArrivalStats | Mapping[str, Any]],
    reused: bool,
    ratio: float = ARRIVAL_RATIO,
) -> dict[str, Any]:
    typed = {name: _as_arrival_stats(row) for name, row in stats.items()}
    return {
        "ratio": ratio,
        "reused": reused,
        "discovered_rps": dict(discovered or {}),
        "targets": dict(targets),
        "routes": {name: row.as_dict() for name, row in typed.items()},
        "total": merge_arrival_stats(tuple(typed.values())).as_dict(),
    }


