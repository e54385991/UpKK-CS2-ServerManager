"""Ordered cleanup scan phases; state is local to a single scan."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

from modules.models import Server

if TYPE_CHECKING:
    from services.game_cleanup_service import GameCleanupService
    from services.ssh_manager import SSHManager


@dataclass
class CleanupScanState:
    safe_items: list[dict[str, Any]] = field(default_factory=list)
    archive_items: list[dict[str, Any]] = field(default_factory=list)
    workshop_items: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    safe_count: int = 0
    archive_found: int = 0
    workshop_found: int = 0
    workshop_size: int = 0


async def _scan_workshop(
    self: GameCleanupService,
    ssh_manager: SSHManager,
    server: Server,
    state: CleanupScanState,
    child_timeout: int,
) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "phase", "phase": "workshop", "message": "Scanning Steam Workshop"}
    workshop_records: list[dict[str, Any]] = []
    state.workshop_found = 0
    async for event in self._scan_phase_events(
        ssh_manager,
        server,
        self._find_children_command(self.workshop_dir(server)),
        "workshop",
        "workshop",
        timeout=child_timeout,
    ):
        kind = event.get("type")
        if kind == "heartbeat":
            yield {"type": "heartbeat"}
            continue
        if kind == "progress":
            yield {
                "type": "batch",
                "category": "workshop",
                "phase": "workshop",
                "found": int(event["found"]),
                "size": int(event["size"]),
            }
            continue
        if kind == "error":
            yield {"type": "error", "message": event.get("message")}
            return
        if kind == "complete":
            workshop_records = list(event.get("listed") or [])
            state.workshop_found = int(event.get("found") or 0)
            state.truncated = state.truncated or bool(event.get("truncated"))
    state.workshop_items = [
        self._with_category(record, "workshop", "Steam Workshop content", "danger")
        for record in workshop_records
    ]
    state.workshop_size = await self._directory_size(ssh_manager, self.workshop_dir(server))
    if state.workshop_size == 0:
        state.workshop_size = sum(item["size"] for item in state.workshop_items)
    yield {
        "type": "batch",
        "category": "workshop",
        "phase": "workshop",
        "found": max(len(state.workshop_items), state.workshop_found),
        "size": state.workshop_size,
    }


async def _scan_archives(
    self: GameCleanupService,
    ssh_manager: SSHManager,
    server: Server,
    state: CleanupScanState,
    archive_patterns: tuple[str, ...],
) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "phase", "phase": "archives", "message": "Scanning leftover archives"}
    archive_records: list[dict[str, Any]] = []
    state.archive_found = 0
    archive_size = 0
    async for event in self._scan_phase_events(
        ssh_manager,
        server,
        self._find_named_files_command(server, archive_patterns),
        "archive",
        "archives",
    ):
        kind = event.get("type")
        if kind == "heartbeat":
            yield {"type": "heartbeat"}
            continue
        if kind == "progress":
            yield {
                "type": "batch",
                "category": "archive",
                "phase": "archives",
                "found": int(event["found"]),
                "size": int(event["size"]),
            }
            continue
        if kind == "error":
            yield {"type": "error", "message": event.get("message")}
            return
        if kind == "complete":
            archive_records = list(event.get("listed") or [])
            state.archive_found = int(event.get("found") or 0)
            archive_size = int(event.get("size") or 0)
            state.truncated = state.truncated or bool(event.get("truncated"))
    state.archive_items = [
        self._with_category(record, "archive", "Common leftover archive file", "confirm")
        for record in archive_records
        if record["type"] == "file"
        and self.is_archive_path(record["path"])
        and not self.is_workshop_path(server, record["path"])
        and not any(self.is_under(item["path"], record["path"]) for item in state.safe_items)
    ]
    state.archive_items.sort(key=lambda item: item["path"])
    yield {
        "type": "batch",
        "category": "archive",
        "phase": "archives",
        "found": max(len(state.archive_items), state.archive_found),
        "size": archive_size,
    }


async def _scan_logs(
    self: GameCleanupService,
    ssh_manager: SSHManager,
    server: Server,
    state: CleanupScanState,
    log_patterns: tuple[str, ...],
) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "phase", "phase": "logs", "message": "Scanning leftover log files"}
    log_records: list[dict[str, Any]] = []
    log_found = 0
    async for event in self._scan_phase_events(
        ssh_manager,
        server,
        self._find_named_files_command(server, log_patterns),
        "safe",
        "logs",
    ):
        kind = event.get("type")
        if kind == "heartbeat":
            yield {"type": "heartbeat"}
            continue
        if kind == "progress":
            yield {
                "type": "batch",
                "category": "safe",
                "phase": "logs",
                "found": len(state.safe_items) + int(event["found"]),
                "size": sum(item["size"] for item in state.safe_items) + int(event["size"]),
            }
            continue
        if kind == "error":
            yield {"type": "error", "message": event.get("message")}
            return
        if kind == "complete":
            log_records = list(event.get("listed") or [])
            log_found = int(event.get("found") or 0)
            state.truncated = state.truncated or bool(event.get("truncated"))
    safe_root_paths = [root for root, _ in self.safe_roots(server)]
    extra_logs = [
        self._with_category(record, "safe", "Game log file", "safe")
        for record in log_records
        if record["type"] == "file"
        and not any(self.is_under(root, record["path"]) for root in safe_root_paths)
        and not self.is_workshop_path(server, record["path"])
    ]
    state.safe_items.extend(extra_logs)
    state.safe_items = self._filter_nested_items(state.safe_items)
    state.safe_count = max(len(state.safe_items), log_found)
    yield {
        "type": "batch",
        "category": "safe",
        "phase": "logs",
        "found": state.safe_count,
        "size": sum(item["size"] for item in state.safe_items),
    }


async def _scan_safe_roots(
    self: GameCleanupService,
    ssh_manager: SSHManager,
    server: Server,
    state: CleanupScanState,
    child_timeout: int,
) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "phase", "phase": "safe_roots", "message": "Scanning approved log folders"}
    for safe_root, reason in self.safe_roots(server):
        records: list[dict[str, Any]] = []
        async for event in self._iter_find_records(
            ssh_manager,
            server,
            self._find_children_command(safe_root),
            timeout=child_timeout,
        ):
            kind = event.get("type")
            if kind == "heartbeat":
                yield {"type": "heartbeat"}
                continue
            if kind == "progress":
                yield {
                    "type": "batch",
                    "category": "safe",
                    "phase": "safe_roots",
                    "found": len(state.safe_items) + int(event.get("found") or 0),
                    "size": sum(item["size"] for item in state.safe_items)
                    + int(event.get("size") or 0),
                }
                continue
            if kind == "error":
                yield {"type": "error", "message": event.get("message")}
                return
            if kind == "complete":
                records = list(event.get("listed") or [])
                state.truncated = state.truncated or bool(event.get("truncated"))
        state.safe_items.extend(
            self._with_category(record, "safe", reason, "safe")
            for record in records
            if not self.is_workshop_path(server, record["path"])
            or self.is_workshop_temp_path(server, record["path"])
        )
        yield {
            "type": "batch",
            "category": "safe",
            "phase": "safe_roots",
            "found": len(state.safe_items),
            "size": sum(item["size"] for item in state.safe_items),
        }
