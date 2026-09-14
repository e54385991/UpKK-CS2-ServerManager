"""Transport-independent inbox snapshot rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class InboxRecordRow:
    """One operation plus the message the tray already showed."""

    record: dict[str, Any]
    latest_message: str | None
    queue_position: int = 0


@dataclass(slots=True)
class ServerInboxSlice:
    """Active, failed, and completed rows for one authorized server."""

    active: list[InboxRecordRow]
    failed: list[InboxRecordRow]
    completed: list[InboxRecordRow]


@dataclass(slots=True)
class HubInboxSnapshot:
    """Batch hub read for a set of servers. Not cached across users."""

    slices: dict[int, ServerInboxSlice]
    memory_scans: int = 1


@dataclass(slots=True)
class InboxItemData:
    record: dict[str, Any]
    server_name: str
    queue_position: int
    latest_message: str | None


@dataclass(slots=True)
class InboxPayload:
    """Service result before HTTP DTO mapping."""

    items: list[InboxItemData]
    completed_items: list[InboxItemData]
    failed_items: list[InboxItemData]
    import_jobs: list[Any] = field(default_factory=list)
    completed_retention_days: int = 7
    failed_retention_days: int = 7
