"""
Game directory cleanup service.

Scans and deletes only server-side approved cleanup candidates under a server's
configured game directory.
"""

import posixpath
import shlex
from typing import Any, Dict, Iterable, List, Optional, Tuple

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

WORKSHOP_CONFIRMATION_TEXT = "DELETE WORKSHOP"


class GameCleanupService:
    """Scan and delete approved log, temp, archive, and workshop cleanup items."""

    def game_dir(self, server) -> str:
        return posixpath.normpath((server.game_directory or "").rstrip("/"))

    def csgo_logs_dir(self, server) -> str:
        return posixpath.join(self.game_dir(server), "cs2/game/csgo/logs")

    def css_logs_dir(self, server) -> str:
        return posixpath.join(
            self.game_dir(server),
            "cs2/game/csgo/addons/counterstrikesharp/logs",
        )

    def workshop_dir(self, server) -> str:
        return posixpath.join(
            self.game_dir(server),
            "cs2/game/bin/linuxsteamrt64/steamapps/workshop",
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

    def _parse_find_output(self, server, output: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for raw_record in output.split("\0"):
            if not raw_record:
                continue
            parts = raw_record.split("\t", 3)
            if len(parts) != 4:
                continue
            record = self._record_from_parts(server, *parts)
            if record:
                records.append(record)
        return records

    async def _run_find(
        self, ssh_manager, command: str, timeout: int = 120
    ) -> Tuple[bool, str, str]:
        success, stdout, stderr = await ssh_manager.execute_command(command, timeout=timeout)
        if not success:
            return False, [], stderr or "Failed to scan cleanup candidates"
        return True, stdout, ""

    async def _find_records(
        self, ssh_manager, server, command: str, timeout: int = 120
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        success, output, error = await self._run_find(ssh_manager, command, timeout=timeout)
        if not success:
            return False, [], error
        return True, self._parse_find_output(server, output), ""

    async def _scan_direct_children(
        self, ssh_manager, server, path: str
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        quoted_path = shlex.quote(path)
        command = (
            f"if [ -d {quoted_path} ]; then "
            f"find {quoted_path} -mindepth 1 -maxdepth 1 ! -type l -exec sh -c "
            "'for item do "
            'if [ -d "$item" ]; then kind=d; else kind=f; fi; '
            'size=$(du -sb -- "$item" 2>/dev/null | cut -f1); '
            '[ -n "$size" ] || size=0; '
            'modified=$(stat -c %Y -- "$item" 2>/dev/null || printf 0); '
            'printf "%s\\t%s\\t%s\\t%s\\0" "$kind" "$size" "$modified" "$item"; '
            "done' sh {} +; "
            "fi"
        )
        return await self._find_records(ssh_manager, server, command, timeout=300)

    async def _scan_general_files(
        self, ssh_manager, server
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        game_dir = self.game_dir(server)
        workshop_dir = self.workshop_dir(server)
        command = (
            f"find {shlex.quote(game_dir)} "
            f"-path {shlex.quote(workshop_dir)} -prune -o "
            "-type f -printf '%y\\t%s\\t%T@\\t%p\\0'"
        )
        return await self._find_records(ssh_manager, server, command, timeout=180)

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

    async def scan(self, ssh_manager, server) -> Tuple[bool, Dict[str, Any], str]:
        if self.game_dir(server) in ("", ".", "/"):
            return False, {}, "Server game directory is not safe for cleanup scanning."

        success, message = await self._ensure_connected(ssh_manager, server)
        if not success:
            return False, {}, f"Connection failed: {message}"

        safe_items: List[Dict[str, Any]] = []
        for safe_root, reason in self.safe_roots(server):
            success, records, error = await self._scan_direct_children(
                ssh_manager, server, safe_root
            )
            if not success:
                return False, {}, error
            safe_items.extend(
                self._with_category(record, "safe", reason, "safe")
                for record in records
                if not self.is_workshop_path(server, record["path"])
                or self.is_workshop_temp_path(server, record["path"])
            )

        success, general_records, error = await self._scan_general_files(ssh_manager, server)
        if not success:
            return False, {}, error

        safe_root_paths = [root for root, _ in self.safe_roots(server)]
        safe_items.extend(
            self._with_category(record, "safe", "Game log file", "safe")
            for record in general_records
            if record["type"] == "file"
            and record["path"].lower().endswith(".log")
            and not any(self.is_under(root, record["path"]) for root in safe_root_paths)
            and not self.is_workshop_path(server, record["path"])
        )
        safe_items = self._filter_nested_items(safe_items)

        archive_items = [
            self._with_category(record, "archive", "Common leftover archive file", "confirm")
            for record in general_records
            if record["type"] == "file"
            and self.is_archive_path(record["path"])
            and not self.is_workshop_path(server, record["path"])
            and not any(self.is_under(item["path"], record["path"]) for item in safe_items)
        ]
        archive_items.sort(key=lambda item: item["path"])

        success, workshop_records, error = await self._scan_direct_children(
            ssh_manager,
            server,
            self.workshop_dir(server),
        )
        if not success:
            return False, {}, error
        workshop_items = [
            self._with_category(record, "workshop", "Steam Workshop content", "danger")
            for record in workshop_records
        ]

        total_size = sum(item["size"] for item in safe_items)
        total_size += sum(item["size"] for item in archive_items)
        total_size += sum(item["size"] for item in workshop_items)

        return (
            True,
            {
                "safe_items": safe_items,
                "archive_items": archive_items,
                "workshop_summary": {
                    "path": self.workshop_dir(server),
                    "item_count": len(workshop_items),
                    "size": sum(item["size"] for item in workshop_items),
                    "items": workshop_items,
                },
                "total_size": total_size,
            },
            "",
        )

    async def _delete_items(
        self, ssh_manager, server, items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        failed_items: List[Dict[str, str]] = []
        deleted_count = 0
        freed_bytes_estimate = 0

        for item in self._filter_nested_items(items):
            path = item["path"]
            if not self.is_path_safe(server, path) or self.normalize_path(path) == self.game_dir(
                server
            ):
                failed_items.append(
                    {"path": path, "error": "Path is outside the allowed cleanup scope"}
                )
                continue
            success, error = await ssh_manager.delete_path(path, server)
            if success:
                deleted_count += 1
                freed_bytes_estimate += int(item.get("size") or 0)
            else:
                failed_items.append({"path": path, "error": error})

        return {
            "deleted_count": deleted_count,
            "freed_bytes_estimate": freed_bytes_estimate,
            "failed_items": failed_items,
        }

    async def delete(
        self,
        ssh_manager,
        server,
        mode: str,
        paths: Optional[List[str]] = None,
        confirmation_text: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any], str]:
        success, scan_data, error = await self.scan(ssh_manager, server)
        if not success:
            return False, {}, error

        if mode == "safe":
            items = scan_data["safe_items"]
        elif mode == "archives":
            requested_paths = {self.normalize_path(path) for path in (paths or [])}
            if not requested_paths:
                return False, {}, "Please select at least one archive to delete."
            archive_by_path = {item["path"]: item for item in scan_data["archive_items"]}
            invalid_paths = sorted(path for path in requested_paths if path not in archive_by_path)
            if invalid_paths:
                return (
                    False,
                    {},
                    f"One or more selected archives are no longer valid cleanup candidates: {', '.join(invalid_paths[:3])}",
                )
            items = [archive_by_path[path] for path in sorted(requested_paths)]
        elif mode == "workshop":
            if confirmation_text != WORKSHOP_CONFIRMATION_TEXT:
                return False, {}, f"Confirmation text must be {WORKSHOP_CONFIRMATION_TEXT}."
            items = scan_data["workshop_summary"]["items"]
        else:
            return False, {}, "Invalid cleanup mode."

        result = await self._delete_items(ssh_manager, server, items)
        failed_count = len(result["failed_items"])
        if failed_count:
            message = f"Deleted {result['deleted_count']} item(s), {failed_count} item(s) failed."
        else:
            message = f"Deleted {result['deleted_count']} item(s)."
        result.update(
            {
                "success": failed_count == 0,
                "message": message,
            }
        )
        return failed_count == 0, result, ""


game_cleanup_service = GameCleanupService()
