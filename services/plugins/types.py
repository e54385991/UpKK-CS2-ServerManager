"""Transport-independent plugin service results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DescriptionSyncAction = Literal["updated", "unchanged", "skipped", "failed"]
ReleaseSyncMode = Literal["sync", "scan"]
ReleaseSyncAction = Literal["updated", "unchanged", "skipped", "failed"]
ReleaseIssueCode = Literal[
    "invalid_url",
    "repository_missing",
    "release_missing",
    "no_linux_asset",
    "manual_selection_required",
    "archive_invalid",
    "upstream_error",
]


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


@dataclass(frozen=True)
class ReleaseSyncItem:
    """Outcome of synchronizing or scanning one marketplace listing's release."""

    plugin_id: int
    title: str
    github_url: str
    action: ReleaseSyncAction
    tag: str | None = None
    rule: str | None = None
    selected_assets: list[str] = field(default_factory=list)
    issue_code: ReleaseIssueCode | None = None
    cleanable: bool = False
    message: str | None = None
