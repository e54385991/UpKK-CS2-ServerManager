"""Transport-independent plugin service results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DescriptionSyncAction = Literal["updated", "unchanged", "skipped", "failed"]


@dataclass(frozen=True)
class DescriptionSyncItem:
    """Outcome of refreshing one marketplace listing from its GitHub README."""

    plugin_id: int
    title: str
    github_url: str
    action: DescriptionSyncAction
    message: str | None = None


@dataclass(frozen=True)
class DescriptionSyncResult:
    """Aggregate outcome of a marketplace description sync."""

    total: int
    updated: int
    unchanged: int
    skipped: int
    failed: int
    remaining: int = 0
    items: list[DescriptionSyncItem] = field(default_factory=list)
