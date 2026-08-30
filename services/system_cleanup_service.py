"""Linux system-junk cleanup and automatic log-retention policy.

Game-directory cleanup stays in ``game_cleanup_service``. This module only
runs a fixed allowlist of common Linux maintenance commands. User-supplied
paths are never interpolated into shell commands.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Iterable

from services.game_cleanup_service import game_cleanup_service
from services.host_initialization import SshManagerHostRunner

LOG_CLEANUP_ACTION = "log_cleanup"
LOG_CLEANUP_TASK_NAME = "Automatic log cleanup"
DEFAULT_RETAIN_DAYS = 7
DEFAULT_SCHEDULE_VALUE = "03:30"
DEFAULT_TARGETS = ("game_logs",)

SYSTEM_TARGET_IDS = (
    "game_logs",
    "thumbnails",
    "apt_cache",
    "journal",
    "tmp",
    "crash",
    "rotated_logs",
)
PRIVILEGED_TARGETS = frozenset({"apt_cache", "journal", "tmp", "crash", "rotated_logs"})

_TARGET_META: dict[str, dict[str, str]] = {
    "game_logs": {
        "title": "Old game / plugin logs",
        "reason": "Files older than the retain window under approved CS2 log directories.",
    },
    "thumbnails": {
        "title": "User thumbnail cache",
        "reason": "~/.cache/thumbnails for the SSH user.",
    },
    "apt_cache": {
        "title": "Package manager cache",
        "reason": "apt-get clean, or dnf/yum clean all when apt is absent.",
    },
    "journal": {
        "title": "systemd journal",
        "reason": "journalctl --vacuum-time using the retain window.",
    },
    "tmp": {
        "title": "Old files in /tmp and /var/tmp",
        "reason": "Regular files older than the retain window. tmux/screen sockets are skipped.",
    },
    "crash": {
        "title": "Kernel crash dumps",
        "reason": "Contents of /var/crash.",
    },
    "rotated_logs": {
        "title": "Rotated /var/log archives",
        "reason": "Compressed or numbered rotated logs only. Live log files are kept.",
    },
}


def clamp_retain_days(value: int | None) -> int:
    try:
        days = int(value if value is not None else DEFAULT_RETAIN_DAYS)
    except TypeError, ValueError:
        days = DEFAULT_RETAIN_DAYS
    return max(1, min(days, 90))


def normalize_schedule_value(value: str | None) -> str:
    raw = (value or "").strip() or DEFAULT_SCHEDULE_VALUE
    if not re.fullmatch(r"\d{1,2}:\d{2}", raw):
        raise ValueError("Daily cleanup time must be HH:MM.")
    hour, minute = (int(part) for part in raw.split(":", 1))
    if hour > 23 or minute > 59:
        raise ValueError("Daily cleanup time must be a valid 24-hour HH:MM.")
    return f"{hour:02d}:{minute:02d}"


def normalize_targets(values: Iterable[str] | None) -> list[str]:
    if not values:
        return list(DEFAULT_TARGETS)
    seen: list[str] = []
    for raw in values:
        key = str(raw or "").strip()
        if key not in SYSTEM_TARGET_IDS:
            raise ValueError(f"Unknown cleanup target: {key}")
        if key not in seen:
            seen.append(key)
    return seen or list(DEFAULT_TARGETS)


def needs_privilege(target_id: str) -> bool:
    return target_id in PRIVILEGED_TARGETS


def can_apply_target(target_id: str, privilege: str) -> bool:
    if not needs_privilege(target_id):
        return True
    return privilege in {"root", "sudo"}


def target_command(target_id: str, retain_days: int) -> str:
    days = clamp_retain_days(retain_days)
    if target_id == "thumbnails":
        return 'rm -rf -- "$HOME/.cache/thumbnails"/*'
    if target_id == "apt_cache":
        return (
            "if command -v apt-get >/dev/null 2>&1; then apt-get clean; "
            "elif command -v dnf >/dev/null 2>&1; then dnf -y clean all; "
            "elif command -v yum >/dev/null 2>&1; then yum -y clean all; "
            "else echo 'No apt/dnf/yum cache tool on this host.'; fi"
        )
    if target_id == "journal":
        return f"journalctl --vacuum-time={days}d"
    if target_id == "tmp":
        return (
            f"find /tmp /var/tmp -xdev -mindepth 1 -type f -mtime +{days} "
            "! -name 'tmux-*' ! -name 'screen-*' "
            r"! -path '/tmp/systemd-private*' -delete"
        )
    if target_id == "crash":
        return "if [ -d /var/crash ]; then find /var/crash -xdev -mindepth 1 ! -type l -delete; fi"
    if target_id == "rotated_logs":
        return (
            "find /var/log -xdev -maxdepth 2 -type f "
            r"\( -name '*.gz' -o -name '*.xz' -o -name '*.1' -o -name '*.old' \) -delete"
        )
    raise ValueError(f"No remote command for target: {target_id}")


def manual_execute_commands(targets: Iterable[str], retain_days: int) -> list[str]:
    days = clamp_retain_days(retain_days)
    commands: list[str] = []
    for target_id in normalize_targets(targets):
        if target_id == "game_logs":
            commands.append(
                f"# Game logs older than {days} days are cleaned by the panel over SSH."
            )
            continue
        command = target_command(target_id, days)
        if needs_privilege(target_id):
            commands.append(f"sudo sh -c {shlex.quote(command)}")
        else:
            commands.append(command)
    return commands


def manual_setup_commands(
    targets: Iterable[str], retain_days: int, schedule_value: str
) -> list[str]:
    hour, minute = normalize_schedule_value(schedule_value).split(":")
    execute = [
        line for line in manual_execute_commands(targets, retain_days) if not line.startswith("#")
    ]
    joined = " ; ".join(execute) if execute else "true"
    return [
        "# 1) Grant this SSH user passwordless sudo, or save a sudo/root password on Host config.",
        "# 2) Or install a root crontab that runs the same cleanup:",
        "sudo crontab -e",
        f"# Daily at {hour}:{minute}",
        f"{int(minute)} {int(hour)} * * * {joined}",
    ]


def parse_size(output: str) -> int:
    text = (output or "").strip().splitlines()
    if not text:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)\s*([KMGTPE]?)i?B?", text[-1], re.IGNORECASE)
    if not match:
        digits = re.search(r"(\d+)", text[-1])
        return int(digits.group(1)) if digits else 0
    value = float(match.group(1))
    unit = match.group(2).upper()
    factor = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}.get(unit, 1)
    if unit:
        return int(value * factor)
    return int(value)


class SystemCleanupService:
    """Scan and apply allowlisted Linux junk cleanup, plus the auto policy."""

    def policy_from_server(self, server, task=None) -> dict[str, Any]:
        targets = list(getattr(server, "cleanup_targets", None) or DEFAULT_TARGETS)
        try:
            targets = normalize_targets(targets)
        except ValueError:
            targets = list(DEFAULT_TARGETS)
        schedule_value = DEFAULT_SCHEDULE_VALUE
        if task is not None and getattr(task, "schedule_value", None):
            try:
                schedule_value = normalize_schedule_value(task.schedule_value)
            except ValueError:
                schedule_value = DEFAULT_SCHEDULE_VALUE
        return {
            "enabled": bool(getattr(server, "cleanup_auto_enabled", False)),
            "retain_days": clamp_retain_days(getattr(server, "cleanup_retain_days", None)),
            "schedule_value": schedule_value,
            "targets": targets,
            "has_sudo_password": bool(getattr(server, "sudo_password", None)),
            "last_run": getattr(task, "last_run", None) if task is not None else None,
            "next_run": getattr(task, "next_run", None) if task is not None else None,
            "last_status": getattr(task, "last_status", None) if task is not None else None,
            "last_error": getattr(task, "last_error", None) if task is not None else None,
            "run_count": int(getattr(task, "run_count", 0) or 0) if task is not None else 0,
        }

    def _size_command(self, target_id: str) -> str:
        inner = self._raw_size_command(target_id)
        return f"timeout 15 sh -c {shlex.quote(inner)} || echo 0"

    def _raw_size_command(self, target_id: str) -> str:
        if target_id == "thumbnails":
            return 'if [ -d "$HOME/.cache/thumbnails" ]; then du -sb -- "$HOME/.cache/thumbnails" | cut -f1; else echo 0; fi'
        if target_id == "apt_cache":
            return (
                "if [ -d /var/cache/apt/archives ]; then du -sb -- /var/cache/apt/archives | cut -f1; "
                "elif [ -d /var/cache/dnf ]; then du -sb -- /var/cache/dnf | cut -f1; "
                "elif [ -d /var/cache/yum ]; then du -sb -- /var/cache/yum | cut -f1; "
                "else echo 0; fi"
            )
        if target_id == "journal":
            return (
                "if [ -d /var/log/journal ]; then du -sb -- /var/log/journal | cut -f1; "
                "else journalctl --disk-usage 2>/dev/null || echo 0; fi"
            )
        if target_id == "tmp":
            return (
                "size=0; for dir in /tmp /var/tmp; do "
                '[ -d "$dir" ] || continue; '
                'part=$(timeout 8 du -sb -- "$dir" 2>/dev/null | cut -f1); '
                "size=$((size + ${part:-0})); "
                'done; echo "$size"'
            )
        if target_id == "crash":
            return "if [ -d /var/crash ]; then du -sb -- /var/crash | cut -f1; else echo 0; fi"
        if target_id == "rotated_logs":
            return (
                "find /var/log -xdev -maxdepth 2 -type f "
                r"\( -name '*.gz' -o -name '*.xz' -o -name '*.1' -o -name '*.old' \) "
                "-printf '%s\\n' 2>/dev/null | awk '{s+=$1} END {print s+0}'"
            )
        return "echo 0"

    async def _estimate_game_logs(self, ssh_manager, server) -> int:
        total = 0
        for root, _reason in game_cleanup_service.safe_roots(server):
            quoted = shlex.quote(root)
            command = (
                f"if [ -d {quoted} ]; then "
                f"timeout 15 du -sb -- {quoted} 2>/dev/null | cut -f1; "
                "else echo 0; fi"
            )
            success, stdout, _stderr = await ssh_manager.execute_command(command, timeout=20)
            if success:
                total += parse_size(stdout)
        return total

    async def _estimate_target_size(self, ssh_manager, server, target_id: str) -> int:
        if target_id == "game_logs":
            return await self._estimate_game_logs(ssh_manager, server)
        success, stdout, _stderr = await ssh_manager.execute_command(
            self._size_command(target_id),
            timeout=20,
        )
        return parse_size(stdout) if success else 0

    def _scan_payload(
        self,
        server,
        privilege: str,
        days: int,
        targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        privileged_needed = any(item["needs_privilege"] for item in targets)
        return {
            "privilege": privilege,
            "retain_days": days,
            "has_sudo_password": bool(getattr(server, "sudo_password", None)),
            "targets": targets,
            "total_size": sum(item["size"] for item in targets),
            "can_apply_privileged": privilege in {"root", "sudo"},
            "manual_execute": manual_execute_commands(
                [item["id"] for item in targets if item["needs_privilege"]],
                days,
            )
            if privileged_needed and privilege == "none"
            else [],
            "manual_setup": manual_setup_commands(
                [item["id"] for item in targets if item["needs_privilege"]],
                days,
                DEFAULT_SCHEDULE_VALUE,
            )
            if privileged_needed and privilege == "none"
            else [],
        }

    async def iter_scan(self, ssh_manager, server, retain_days: int | None = None):
        days = clamp_retain_days(
            retain_days if retain_days is not None else getattr(server, "cleanup_retain_days", None)
        )
        connected, message = await game_cleanup_service._ensure_connected(ssh_manager, server)
        if not connected:
            yield {"type": "error", "message": f"Connection failed: {message}"}
            return

        runner = SshManagerHostRunner(ssh_manager, server)
        privilege = await runner.resolve_privilege()
        yield {
            "type": "phase",
            "phase": "privilege",
            "message": f"SSH privilege: {privilege}",
        }
        targets: list[dict[str, Any]] = []
        for target_id in SYSTEM_TARGET_IDS:
            meta = _TARGET_META[target_id]
            yield {
                "type": "phase",
                "phase": target_id,
                "message": f"Measuring {meta['title']}",
            }
            size = await self._estimate_target_size(ssh_manager, server, target_id)
            item = {
                "id": target_id,
                "title": meta["title"],
                "reason": meta["reason"],
                "size": size,
                "needs_privilege": needs_privilege(target_id),
                "can_apply": can_apply_target(target_id, privilege),
                "command": (None if target_id == "game_logs" else target_command(target_id, days)),
            }
            targets.append(item)
            yield {"type": "target", "target": item}

        payload = self._scan_payload(server, privilege, days, targets)
        yield {"type": "done", "data": payload}

    async def scan(self, ssh_manager, server, retain_days: int | None = None) -> dict[str, Any]:
        async for event in self.iter_scan(ssh_manager, server, retain_days=retain_days):
            if event.get("type") == "error":
                raise RuntimeError(str(event.get("message") or "System cleanup scan failed"))
            if event.get("type") == "done":
                return event["data"]
        raise RuntimeError("System cleanup scan produced no result")

    async def apply(
        self,
        ssh_manager,
        server,
        target_ids: Iterable[str],
        retain_days: int | None = None,
    ) -> dict[str, Any]:
        days = clamp_retain_days(
            retain_days if retain_days is not None else getattr(server, "cleanup_retain_days", None)
        )
        selected = normalize_targets(target_ids)
        connected, message = await game_cleanup_service._ensure_connected(ssh_manager, server)
        if not connected:
            raise RuntimeError(f"Connection failed: {message}")

        runner = SshManagerHostRunner(ssh_manager, server)
        privilege = await runner.resolve_privilege()

        applied: list[str] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        deleted_count = 0
        freed = 0

        for target_id in selected:
            if not can_apply_target(target_id, privilege):
                skipped.append(
                    {
                        "id": target_id,
                        "error": "This SSH user cannot run privileged cleanup. Use the manual commands.",
                    }
                )
                continue
            if target_id == "game_logs":
                ok, result, error = await game_cleanup_service.purge_old_logs(
                    ssh_manager, server, days
                )
                if error and not result:
                    failed.append({"id": target_id, "error": error})
                    continue
                deleted_count += int(result.get("deleted_count") or 0)
                freed += int(result.get("freed_bytes_estimate") or 0)
                if ok:
                    applied.append(target_id)
                else:
                    failed.append({"id": target_id, "error": result.get("message") or error})
                continue

            command = target_command(target_id, days)
            if needs_privilege(target_id) and privilege != "root":
                code, stdout, stderr = await runner.run_privileged(command, timeout=180)
            else:
                code, stdout, stderr = await runner.run(command, timeout=180)
            if code == 0:
                applied.append(target_id)
            else:
                detail = (stderr or stdout or "Command failed").strip()
                failed.append({"id": target_id, "error": detail[:500]})

        manual_targets = [item["id"] for item in skipped]
        success = not failed and not skipped
        parts = []
        if applied:
            parts.append(f"Cleaned {len(applied)} target(s).")
        if skipped:
            parts.append(f"{len(skipped)} target(s) need root/sudo.")
        if failed:
            parts.append(f"{len(failed)} target(s) failed.")
        return {
            "success": success,
            "message": " ".join(parts) or "Nothing was selected.",
            "privilege": privilege,
            "applied": applied,
            "skipped": skipped,
            "failed": failed,
            "deleted_count": deleted_count,
            "freed_bytes_estimate": freed,
            "manual_execute": manual_execute_commands(manual_targets, days)
            if manual_targets
            else [],
            "manual_setup": (
                manual_setup_commands(manual_targets, days, DEFAULT_SCHEDULE_VALUE)
                if manual_targets
                else []
            ),
        }

    async def run_scheduled(self, ssh_manager, server) -> tuple[bool, str]:
        targets = normalize_targets(getattr(server, "cleanup_targets", None))
        result = await self.apply(
            ssh_manager,
            server,
            targets,
            retain_days=getattr(server, "cleanup_retain_days", None),
        )
        message = result["message"]
        if result["manual_execute"]:
            message = f"{message}\n" + "\n".join(result["manual_execute"])
        return bool(result["success"]), message


system_cleanup_service = SystemCleanupService()
