"""Concrete I/O and budget inputs for hub helpers; state stays on the hub."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.redis_manager import RedisManager


@dataclass(frozen=True)
class OperationDependencies:
    redis_manager: RedisManager
    logger: Logger
    operation_ttl: int
    event_limit: int
    queue_limit: int
    active_statuses: frozenset[str]
    terminal_event_types: frozenset[str]
    trim_events: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    record_ttl: Callable[[dict[str, Any]], int]
