"""Fleet, marketplace, history, and measurement profiles for isolated runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HistoryKind = Literal["empty", "daily", "max"]
MeasureMode = Literal["smoke", "baseline"]
FleetName = Literal["fleet-10", "fleet-100", "fleet-500", "fleet-1000", "fleet-1001"]
MarketName = Literal["market-100", "market-1000", "market-10000"]

OVERVIEW_SERVER_LIMIT = 1000
REDIS_PIPELINE_BATCH = 500
DOWNLOAD_CHUNK_CANDIDATES = (8192, 65536, 262144)
CURRENT_DOWNLOAD_CHUNK = 8192
IMPROVEMENT_GATE = 0.20
REGRESSION_RATIO = 0.05
REGRESSION_FLOOR_MS = 20.0
CPU_RSS_GATE = 0.05
LOCALE_BYTE_GATE = 0.50
DOWNLOAD_THROUGHPUT_GATE = 0.10
INDEX_BUILD_LIMIT_SECONDS = 5.0
CACHE_PRUNE_SCANS_TODAY = 2


@dataclass(frozen=True, slots=True)
class FleetSpec:
    name: FleetName
    servers: int
    online_users: int
    compatibility: bool = False


@dataclass(frozen=True, slots=True)
class MarketSpec:
    name: MarketName
    plugins: int


@dataclass(frozen=True, slots=True)
class MeasureSpec:
    name: MeasureMode
    browser_warmup: int
    browser_measure: int
    browser_rounds: int
    api_warmup_seconds: int
    api_measure_seconds: int
    api_rounds: int
    soak_seconds: int


@dataclass(frozen=True, slots=True)
class OwnerSplit:
    admin: int
    member: int
    tenant_a: int
    tenant_b: int

    @property
    def total(self) -> int:
        return self.admin + self.member + self.tenant_b + self.tenant_a


FLEETS: dict[FleetName, FleetSpec] = {
    "fleet-10": FleetSpec("fleet-10", 10, 1),
    "fleet-100": FleetSpec("fleet-100", 100, 10),
    "fleet-500": FleetSpec("fleet-500", 500, 30),
    "fleet-1000": FleetSpec("fleet-1000", 1000, 1, compatibility=True),
    "fleet-1001": FleetSpec("fleet-1001", 1001, 1, compatibility=True),
}

MARKETS: dict[MarketName, MarketSpec] = {
    "market-100": MarketSpec("market-100", 100),
    "market-1000": MarketSpec("market-1000", 1000),
    "market-10000": MarketSpec("market-10000", 10000),
}

MEASURES: dict[MeasureMode, MeasureSpec] = {
    "smoke": MeasureSpec("smoke", 1, 2, 1, 5, 10, 1, 0),
    "baseline": MeasureSpec("baseline", 5, 30, 3, 60, 300, 3, 3600),
}

LOGIN_NAMESPACES = ("site", "feedback", "login")
OVERVIEW_NAMESPACES = ("site", "feedback", "nav", "shell", "overview")
USERNAMES = ("admin", "member", "tenant_a", "tenant_b")


def split_ownership(total: int) -> OwnerSplit:
    """Give admin the bulk of the fleet; keep two disjoint tenants and a member."""
    if total <= 0:
        return OwnerSplit(0, 0, 0, 0)
    if total == 1:
        return OwnerSplit(1, 0, 0, 0)
    if total == 2:
        return OwnerSplit(1, 1, 0, 0)
    if total == 3:
        return OwnerSplit(1, 1, 1, 0)
    member = max(1, total // 10)
    admin = total - member - 2
    if admin < 1:
        member = 1
        admin = total - 3
    return OwnerSplit(admin, member, 1, 1)


def overview_counted_servers(admin_owned: int) -> int:
    """Preserve the existing overview cap; 1001 servers are a compatibility case."""
    return min(max(0, admin_owned), OVERVIEW_SERVER_LIMIT)


def actor_cycle(online_users: int) -> tuple[str, ...]:
    """Concurrent sessions cover the four roles; extra workers reuse admin."""
    if online_users <= 0:
        return ("admin",)
    if online_users <= len(USERNAMES):
        return USERNAMES[:online_users]
    extra = online_users - len(USERNAMES)
    return USERNAMES + ("admin",) * extra


def history_kind_for_index(index: int, total: int, profile: HistoryKind) -> HistoryKind:
    """Cover empty, daily, and retention-cap servers without maxing every host."""
    if profile == "empty" or total <= 0:
        return "empty"
    if index == total - 1:
        return "max"
    if profile == "max":
        return "daily" if index < max(1, total // 20) else "max"
    if index < max(1, total // 10):
        return "empty"
    return "daily"


def event_count(kind: HistoryKind, status: str) -> int:
    if kind == "empty":
        return 0
    if kind == "max" and status == "running":
        return 300
    if kind == "max":
        return 5
    if status == "running":
        return 8
    return 1


def retained_counts(kind: HistoryKind) -> tuple[int, int, int, int]:
    """Return running, queued, completed, failed counts for one server."""
    if kind == "empty":
        return (0, 0, 0, 0)
    if kind == "max":
        return (1, 10, 100, 100)
    return (1, 2, 5, 2)
