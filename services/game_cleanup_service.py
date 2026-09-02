"""
Game directory cleanup service.

Scans and deletes only server-side approved cleanup candidates under a server's
configured game directory. Find output is parsed incrementally so a large CS2
tree cannot buffer the whole listing into memory or the HTTP response.
"""

from __future__ import annotations

import asyncio
import posixpath
import shlex
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Tuple

try:
    from .game_cleanup_delete import CleanupDeleteMixin
except ImportError:  # compatibility when loaded directly by legacy tests
    from services.game_cleanup_delete import CleanupDeleteMixin

ARCHIVE_EXTENSIONS = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tbz2",
    ".tgz",
    ".txz",
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
)

LOG_NAME_PATTERNS = ("*.log", "*.log.*")
ARCHIVE_NAME_PATTERNS = tuple(f"*{ext}" for ext in ARCHIVE_EXTENSIONS)

WORKSHOP_CONFIRMATION_TEXT = "DELETE WORKSHOP"

# Preview rows returned to the console. Counting continues up to SCAN_PARSE_LIMIT.
SCAN_LIST_LIMIT = 200
SCAN_PARSE_LIMIT = 4000
FIND_TIMEOUT_SECONDS = 45
CHILD_TIMEOUT_SECONDS = 30
SIZE_TIMEOUT_SECONDS = 15
STREAM_CHUNK_BYTES = 65536


class GameCleanupService(CleanupDeleteMixin):
    """Scan and delete approved log, temp, archive, and workshop cleanup items."""

    def game_dir(self, server) -> str:
        return posixpath.normpath((server.game_directory or "").rstrip("/"))

    def csgo_logs_dir(self, server) -> str:
        return posixpath.join(self.game_dir(server), "game/csgo/logs")

    def css_logs_dir(self, server) -> str:
        return posixpath.join(
            self.game_dir(server),
            "game/csgo/addons/counterstrikesharp/logs",
        )

    def workshop_dir(self, server) -> str:
        return posixpath.join(
            self.game_dir(server),
            "game/bin/linuxsteamrt64/steamapps/workshop",
        )

    def workshop_temp_dir(self, server) -> str:
        return posixpath.join(self.workshop_dir(server), "temp")

    def safe_roots(self, server) -> List[Tuple[str, str]]:
        return [
            (self.csgo_logs_dir(server), "CSGO log directory contents"),
            (self.css_logs_dir(server), "CounterStrikeSharp log directory contents"),
            (self.workshop_temp_dir(server), "Steam Workshop temp directory contents"),
        ]

    def normalize_path(self, path: str) -> str:
        return posixpath.normpath((path or "").strip())

    def is_path_safe(self, server, path: str) -> bool:
        game_dir = self.game_dir(server)
        normalized = self.normalize_path(path)
        if game_dir in ("", ".", "/") or not normalized or "\x00" in normalized:
            return False
        if "\n" in normalized or "\r" in normalized:
            return False
        return normalized == game_dir or normalized.startswith(game_dir.rstrip("/") + "/")

    def is_under(self, parent: str, path: str) -> bool:
        parent = self.normalize_path(parent).rstrip("/")
        path = self.normalize_path(path)
        return path == parent or path.startswith(parent + "/")

    def is_archive_path(self, path: str) -> bool:
        lower_path = path.lower()
        return any(lower_path.endswith(ext) for ext in ARCHIVE_EXTENSIONS)

    def is_workshop_path(self, server, path: str) -> bool:
        return self.is_under(self.workshop_dir(server), path)

    def is_workshop_temp_path(self, server, path: str) -> bool:
        return self.is_under(self.workshop_temp_dir(server), path)

    async def _ensure_connected(self, ssh_manager, server) -> Tuple[bool, str]:
        if getattr(ssh_manager, "conn", None):
            return True, ""
        return await ssh_manager.connect(server)

    def _record_from_parts(
        self,
        server,
        file_type: str,
        size: str,
        modified: str,
        path: str,
    ) -> Optional[Dict[str, Any]]:
        normalized_path = self.normalize_path(path)
        if not self.is_path_safe(server, normalized_path):
            return None
        if file_type == "l":
            return None

        try:
            size_value = int(float(size))
        except TypeError, ValueError:
            size_value = 0
        try:
            modified_value = float(modified)
        except TypeError, ValueError:
            modified_value = 0.0

        return {
            "path": normalized_path,
            "name": posixpath.basename(normalized_path.rstrip("/")),
            "type": "directory" if file_type == "d" else "file",
            "size": max(size_value, 0),
            "modified": modified_value,
        }

    def _parse_find_chunk(self, server, chunk: str, carry: str) -> Tuple[List[Dict[str, Any]], str]:
        text = carry + chunk
        records: List[Dict[str, Any]] = []
        parts = text.split("\0")
        carry_out = parts.pop() if parts else ""
        for raw_record in parts:
            if not raw_record:
                continue
            fields = raw_record.split("\t", 3)
            if len(fields) != 4:
                continue
            record = self._record_from_parts(server, *fields)
            if record:
                records.append(record)
        return records, carry_out

    def _parse_find_output(self, server, output: str) -> List[Dict[str, Any]]:
        records, carry = self._parse_find_chunk(server, output, "")
        if carry:
            extra, _ = self._parse_find_chunk(server, "\0", carry)
            records.extend(extra)
        return records

    def _name_predicates(self, patterns: Iterable[str]) -> str:
        joined = " -o ".join(f"-iname {shlex.quote(pattern)}" for pattern in patterns)
        return f"\\( {joined} \\)"

    def _find_named_files_command(self, server, patterns: Iterable[str]) -> str:
        game_dir = shlex.quote(self.game_dir(server))
        workshop_dir = shlex.quote(self.workshop_dir(server))
        names = self._name_predicates(patterns)
        return (
            f"find {game_dir} \\( -path {workshop_dir} -prune \\) -o "
            f"\\( -type f {names} -printf '%y\\t%s\\t%T@\\t%p\\0' \\)"
        )

    def _find_children_command(self, path: str) -> str:
        quoted = shlex.quote(path)
        return (
            f"if [ -d {quoted} ]; then "
            f"find {quoted} -mindepth 1 -maxdepth 1 ! -type l "
            f"-printf '%y\\t%s\\t%T@\\t%p\\0'; "
            "fi"
        )

    async def _iter_command_text(
        self, ssh_manager, command: str, timeout: int
    ) -> AsyncIterator[str]:
        conn = getattr(ssh_manager, "conn", None)
        create_process = getattr(conn, "create_process", None) if conn is not None else None
        if create_process is None:
            success, stdout, stderr = await ssh_manager.execute_command(command, timeout=timeout)
            if not success:
                raise RuntimeError(stderr or "Failed to scan cleanup candidates")
            if stdout:
                yield stdout
            return

        try:
            process = await create_process(command, encoding=None)
        except TypeError:
            process = await create_process(command)

        deadline = asyncio.get_running_loop().time() + timeout
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise RuntimeError("Command timeout")
                try:
                    chunk = await asyncio.wait_for(
                        process.stdout.read(STREAM_CHUNK_BYTES),
                        timeout=min(remaining, 2),
                    )
                except TimeoutError as exc:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise RuntimeError("Command timeout") from exc
                    yield ""
                    continue
                if not chunk:
                    break
                if isinstance(chunk, bytes):
                    yield chunk.decode("utf-8", errors="replace")
                else:
                    yield str(chunk)
            remaining = deadline - asyncio.get_running_loop().time()
            await asyncio.wait_for(process.wait(), timeout=max(1.0, remaining))
        except TimeoutError as exc:
            raise RuntimeError("Command timeout") from exc
        finally:
            closer = getattr(process, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass

    async def _iter_find_records(
        self,
        ssh_manager,
        server,
        command: str,
        timeout: int = FIND_TIMEOUT_SECONDS,
        list_limit: int = SCAN_LIST_LIMIT,
        parse_limit: int = SCAN_PARSE_LIMIT,
    ) -> AsyncIterator[Dict[str, Any]]:
        listed: List[Dict[str, Any]] = []
        found = 0
        total_size = 0
        truncated = False
        carry = ""
        last_emitted = 0

        def consume(records: List[Dict[str, Any]]) -> None:
            nonlocal found, total_size, truncated
            for record in records:
                found += 1
                total_size += int(record.get("size") or 0)
                if len(listed) < list_limit:
                    listed.append(record)
                if found >= parse_limit:
                    truncated = True
                    return

        try:
            async for chunk in self._iter_command_text(ssh_manager, command, timeout):
                if chunk == "":
                    yield {"type": "heartbeat"}
                    continue
                records, carry = self._parse_find_chunk(server, chunk, carry)
                consume(records)
                if found != last_emitted:
                    last_emitted = found
                    yield {
                        "type": "progress",
                        "found": found,
                        "size": total_size,
                        "truncated": truncated,
                    }
                if truncated:
                    break
            if not truncated and carry:
                records, _ = self._parse_find_chunk(server, "\0", carry)
                consume(records)
        except RuntimeError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        yield {
            "type": "complete",
            "listed": listed,
            "found": found,
            "size": total_size,
            "truncated": truncated,
        }

    async def _collect_find_records(
        self,
        ssh_manager,
        server,
        command: str,
        timeout: int = FIND_TIMEOUT_SECONDS,
        list_limit: int = SCAN_LIST_LIMIT,
        parse_limit: int = SCAN_PARSE_LIMIT,
    ) -> Tuple[bool, List[Dict[str, Any]], str, bool, int, int]:
        async for event in self._iter_find_records(
            ssh_manager,
            server,
            command,
            timeout=timeout,
            list_limit=list_limit,
            parse_limit=parse_limit,
        ):
            kind = event.get("type")
            if kind == "error":
                return False, [], str(event.get("message") or "Failed to scan"), False, 0, 0
            if kind == "complete":
                return (
                    True,
                    list(event.get("listed") or []),
                    "",
                    bool(event.get("truncated")),
                    int(event.get("found") or 0),
                    int(event.get("size") or 0),
                )
        return False, [], "Cleanup scan produced no result", False, 0, 0

    async def _run_find(
        self, ssh_manager, command: str, timeout: int = FIND_TIMEOUT_SECONDS
    ) -> Tuple[bool, str, str]:
        success, stdout, stderr = await ssh_manager.execute_command(command, timeout=timeout)
        if not success:
            return False, "", stderr or "Failed to scan cleanup candidates"
        return True, stdout, ""

    async def _find_records(
        self, ssh_manager, server, command: str, timeout: int = FIND_TIMEOUT_SECONDS
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        success, listed, error, _truncated, _found, _size = await self._collect_find_records(
            ssh_manager, server, command, timeout=timeout
        )
        if not success:
            return False, [], error
        return True, listed, ""

    async def _scan_direct_children(
        self, ssh_manager, server, path: str
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        return await self._find_records(
            ssh_manager,
            server,
            self._find_children_command(path),
            timeout=CHILD_TIMEOUT_SECONDS,
        )

    async def _directory_size(self, ssh_manager, path: str) -> int:
        quoted = shlex.quote(path)
        command = (
            f"if [ -d {quoted} ]; then "
            f"timeout {SIZE_TIMEOUT_SECONDS} du -sb -- {quoted} 2>/dev/null | cut -f1; "
            "else echo 0; fi"
        )
        success, stdout, _stderr = await ssh_manager.execute_command(
            command, timeout=SIZE_TIMEOUT_SECONDS + 5
        )
        if not success:
            return 0
        try:
            return max(int((stdout or "0").strip().splitlines()[-1]), 0)
        except TypeError, ValueError, IndexError:
            return 0

    def _with_category(
        self,
        item: Dict[str, Any],
        category: str,
        reason: str,
        danger_level: str,
    ) -> Dict[str, Any]:
        enriched = dict(item)
        enriched.update(
            {
                "category": category,
                "reason": reason,
                "danger_level": danger_level,
            }
        )
        return enriched

    def _filter_nested_items(self, items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sorted_items = sorted(
            items,
            key=lambda item: (item["path"].count("/"), item["path"]),
        )
        kept: List[Dict[str, Any]] = []
        for item in sorted_items:
            if any(
                self.is_under(parent["path"], item["path"]) and parent["path"] != item["path"]
                for parent in kept
            ):
                continue
            kept.append(item)
        kept.sort(key=lambda item: item["path"])
        return kept

    def _empty_scan(self, server) -> Dict[str, Any]:
        return {
            "safe_items": [],
            "archive_items": [],
            "workshop_summary": {
                "path": self.workshop_dir(server),
                "item_count": 0,
                "size": 0,
                "items": [],
            },
            "total_size": 0,
            "safe_item_count": 0,
            "archive_item_count": 0,
            "truncated": False,
        }

    async def _scan_phase_events(
        self,
        ssh_manager,
        server,
        command: str,
        category: str,
        phase: str,
        *,
        timeout: int | None = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        events = (
            self._iter_find_records(ssh_manager, server, command, timeout=timeout)
            if timeout is not None
            else self._iter_find_records(ssh_manager, server, command)
        )
        async for event in events:
            kind = event.get("type")
            if kind == "heartbeat":
                yield {"type": "heartbeat"}
            elif kind == "progress":
                yield {
                    "type": "batch",
                    "category": category,
                    "phase": phase,
                    "found": int(event.get("found") or 0),
                    "size": int(event.get("size") or 0),
                }
            elif kind == "error":
                yield {"type": "error", "message": event.get("message")}
                return
            elif kind == "complete":
                yield {
                    "type": "complete",
                    "listed": list(event.get("listed") or []),
                    "found": int(event.get("found") or 0),
                    "size": int(event.get("size") or 0),
                    "truncated": bool(event.get("truncated")),
                }

    async def iter_scan(self, ssh_manager, server) -> AsyncIterator[Dict[str, Any]]:
        if self.game_dir(server) in ("", ".", "/"):
            yield {
                "type": "error",
                "message": "Server game directory is not safe for cleanup scanning.",
            }
            return

        success, message = await self._ensure_connected(ssh_manager, server)
        if not success:
            yield {"type": "error", "message": f"Connection failed: {message}"}
            return

        truncated = False
        safe_items: List[Dict[str, Any]] = []
        yield {"type": "phase", "phase": "safe_roots", "message": "Scanning approved log folders"}
        for safe_root, reason in self.safe_roots(server):
            records: List[Dict[str, Any]] = []
            async for event in self._iter_find_records(
                ssh_manager,
                server,
                self._find_children_command(safe_root),
                timeout=CHILD_TIMEOUT_SECONDS,
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
                        "found": len(safe_items) + int(event.get("found") or 0),
                        "size": sum(item["size"] for item in safe_items)
                        + int(event.get("size") or 0),
                    }
                    continue
                if kind == "error":
                    yield {"type": "error", "message": event.get("message")}
                    return
                if kind == "complete":
                    records = list(event.get("listed") or [])
                    truncated = truncated or bool(event.get("truncated"))
            safe_items.extend(
                self._with_category(record, "safe", reason, "safe")
                for record in records
                if not self.is_workshop_path(server, record["path"])
                or self.is_workshop_temp_path(server, record["path"])
            )
            yield {
                "type": "batch",
                "category": "safe",
                "phase": "safe_roots",
                "found": len(safe_items),
                "size": sum(item["size"] for item in safe_items),
            }

        yield {"type": "phase", "phase": "logs", "message": "Scanning leftover log files"}
        log_records: List[Dict[str, Any]] = []
        log_found = 0
        async for event in self._scan_phase_events(
            ssh_manager,
            server,
            self._find_named_files_command(server, LOG_NAME_PATTERNS),
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
                    "found": len(safe_items) + int(event["found"]),
                    "size": sum(item["size"] for item in safe_items) + int(event["size"]),
                }
                continue
            if kind == "error":
                yield {"type": "error", "message": event.get("message")}
                return
            if kind == "complete":
                log_records = list(event.get("listed") or [])
                log_found = int(event.get("found") or 0)
                truncated = truncated or bool(event.get("truncated"))
        safe_root_paths = [root for root, _ in self.safe_roots(server)]
        extra_logs = [
            self._with_category(record, "safe", "Game log file", "safe")
            for record in log_records
            if record["type"] == "file"
            and not any(self.is_under(root, record["path"]) for root in safe_root_paths)
            and not self.is_workshop_path(server, record["path"])
        ]
        safe_items.extend(extra_logs)
        safe_items = self._filter_nested_items(safe_items)
        safe_count = max(len(safe_items), log_found)
        yield {
            "type": "batch",
            "category": "safe",
            "phase": "logs",
            "found": safe_count,
            "size": sum(item["size"] for item in safe_items),
        }

        yield {"type": "phase", "phase": "archives", "message": "Scanning leftover archives"}
        archive_records: List[Dict[str, Any]] = []
        archive_found = 0
        archive_size = 0
        async for event in self._scan_phase_events(
            ssh_manager,
            server,
            self._find_named_files_command(server, ARCHIVE_NAME_PATTERNS),
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
                archive_found = int(event.get("found") or 0)
                archive_size = int(event.get("size") or 0)
                truncated = truncated or bool(event.get("truncated"))
        archive_items = [
            self._with_category(record, "archive", "Common leftover archive file", "confirm")
            for record in archive_records
            if record["type"] == "file"
            and self.is_archive_path(record["path"])
            and not self.is_workshop_path(server, record["path"])
            and not any(self.is_under(item["path"], record["path"]) for item in safe_items)
        ]
        archive_items.sort(key=lambda item: item["path"])
        yield {
            "type": "batch",
            "category": "archive",
            "phase": "archives",
            "found": max(len(archive_items), archive_found),
            "size": archive_size,
        }

        yield {"type": "phase", "phase": "workshop", "message": "Scanning Steam Workshop"}
        workshop_records: List[Dict[str, Any]] = []
        workshop_found = 0
        async for event in self._scan_phase_events(
            ssh_manager,
            server,
            self._find_children_command(self.workshop_dir(server)),
            "workshop",
            "workshop",
            timeout=CHILD_TIMEOUT_SECONDS,
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
                workshop_found = int(event.get("found") or 0)
                truncated = truncated or bool(event.get("truncated"))
        workshop_items = [
            self._with_category(record, "workshop", "Steam Workshop content", "danger")
            for record in workshop_records
        ]
        workshop_size = await self._directory_size(ssh_manager, self.workshop_dir(server))
        if workshop_size == 0:
            workshop_size = sum(item["size"] for item in workshop_items)
        yield {
            "type": "batch",
            "category": "workshop",
            "phase": "workshop",
            "found": max(len(workshop_items), workshop_found),
            "size": workshop_size,
        }

        data = {
            "safe_items": safe_items,
            "archive_items": archive_items,
            "workshop_summary": {
                "path": self.workshop_dir(server),
                "item_count": max(len(workshop_items), workshop_found),
                "size": workshop_size,
                "items": workshop_items,
            },
            "total_size": sum(item["size"] for item in safe_items)
            + sum(item["size"] for item in archive_items)
            + workshop_size,
            "safe_item_count": max(len(safe_items), safe_count),
            "archive_item_count": max(len(archive_items), archive_found),
            "truncated": truncated,
        }
        yield {"type": "done", "data": data}

    async def scan(self, ssh_manager, server) -> Tuple[bool, Dict[str, Any], str]:
        async for event in self.iter_scan(ssh_manager, server):
            if event.get("type") == "error":
                return False, {}, str(event.get("message") or "Cleanup scan failed")
            if event.get("type") == "done":
                return True, event.get("data") or self._empty_scan(server), ""
        return False, {}, "Cleanup scan produced no result"


game_cleanup_service = GameCleanupService()
