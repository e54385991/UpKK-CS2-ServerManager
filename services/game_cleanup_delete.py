"""Deletion workflows for game cleanup."""

from __future__ import annotations

import posixpath
import shlex
from typing import Any, Dict, Iterable, List, Optional, Tuple

LOG_NAME_PATTERNS = ("*.log", "*.log.*")
WORKSHOP_CONFIRMATION_TEXT = "DELETE WORKSHOP"


class CleanupDeleteMixin:
    """Deletion and retention workflows composed into GameCleanupService."""

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

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

    async def _collect_safe_items(
        self, ssh_manager, server
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        items: List[Dict[str, Any]] = []
        for safe_root, reason in self.safe_roots(server):
            ok, records, error = await self._scan_direct_children(ssh_manager, server, safe_root)
            if not ok:
                return False, [], error
            items.extend(
                self._with_category(record, "safe", reason, "safe")
                for record in records
                if not self.is_workshop_path(server, record["path"])
                or self.is_workshop_temp_path(server, record["path"])
            )
        ok, log_records, error = await self._find_records(
            ssh_manager,
            server,
            self._find_named_files_command(server, LOG_NAME_PATTERNS),
        )
        if not ok:
            return False, [], error
        safe_root_paths = [root for root, _ in self.safe_roots(server)]
        items.extend(
            self._with_category(record, "safe", "Game log file", "safe")
            for record in log_records
            if record["type"] == "file"
            and not any(self.is_under(root, record["path"]) for root in safe_root_paths)
            and not self.is_workshop_path(server, record["path"])
        )
        return True, self._filter_nested_items(items), ""

    def _archive_items_from_paths(
        self, server, paths: Iterable[str]
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        items: List[Dict[str, Any]] = []
        invalid: List[str] = []
        for raw in paths:
            path = self.normalize_path(raw)
            if (
                not path
                or not self.is_path_safe(server, path)
                or not self.is_archive_path(path)
                or self.is_workshop_path(server, path)
            ):
                invalid.append(path or raw)
                continue
            items.append(
                self._with_category(
                    {
                        "path": path,
                        "name": posixpath.basename(path),
                        "type": "file",
                        "size": 0,
                        "modified": 0.0,
                    },
                    "archive",
                    "Common leftover archive file",
                    "confirm",
                )
            )
        return items, invalid

    async def delete(
        self,
        ssh_manager,
        server,
        mode: str,
        paths: Optional[List[str]] = None,
        confirmation_text: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any], str]:
        success, message = await self._ensure_connected(ssh_manager, server)
        if not success:
            return False, {}, f"Connection failed: {message}"

        if mode == "safe":
            ok, items, error = await self._collect_safe_items(ssh_manager, server)
            if not ok:
                return False, {}, error
        elif mode == "archives":
            items, invalid_paths = self._archive_items_from_paths(server, paths or [])
            if not items and not (paths or []):
                return False, {}, "Please select at least one archive to delete."
            if invalid_paths:
                return (
                    False,
                    {},
                    f"One or more selected archives are no longer valid cleanup candidates: {', '.join(invalid_paths[:3])}",
                )
        elif mode == "workshop":
            if confirmation_text != WORKSHOP_CONFIRMATION_TEXT:
                return False, {}, f"Confirmation text must be {WORKSHOP_CONFIRMATION_TEXT}."
            ok, records, error = await self._scan_direct_children(
                ssh_manager, server, self.workshop_dir(server)
            )
            if not ok:
                return False, {}, error
            items = [
                self._with_category(record, "workshop", "Steam Workshop content", "danger")
                for record in records
            ]
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

    async def purge_old_logs(
        self,
        ssh_manager,
        server,
        retain_days: int,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """Delete files older than ``retain_days`` under approved log roots only."""
        days = max(1, min(int(retain_days), 90))
        if self.game_dir(server) in ("", ".", "/"):
            return False, {}, "Server game directory is not safe for cleanup scanning."

        success, message = await self._ensure_connected(ssh_manager, server)
        if not success:
            return False, {}, f"Connection failed: {message}"

        items: List[Dict[str, Any]] = []
        for root, reason in self.safe_roots(server):
            quoted = shlex.quote(root)
            command = (
                f"if [ -d {quoted} ]; then "
                f"find {quoted} -type f -mtime +{days} -printf '%y\\t%s\\t%T@\\t%p\\0'; "
                "fi"
            )
            ok, records, error = await self._find_records(ssh_manager, server, command)
            if not ok:
                return False, {}, error
            items.extend(
                self._with_category(record, "safe", reason, "safe")
                for record in records
                if record["type"] == "file" and self.is_under(root, record["path"])
            )

        result = await self._delete_items(ssh_manager, server, items)
        failed_count = len(result["failed_items"])
        result.update(
            {
                "success": failed_count == 0,
                "message": (
                    f"Deleted {result['deleted_count']} old log file(s)."
                    if not failed_count
                    else (
                        f"Deleted {result['deleted_count']} old log file(s), "
                        f"{failed_count} item(s) failed."
                    )
                ),
            }
        )
        return failed_count == 0, result, ""
