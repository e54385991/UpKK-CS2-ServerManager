"""Compatibility helpers for Linux hosts and legacy CS2 plugins."""

from __future__ import annotations

import posixpath
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from modules.execstack import DEFAULT_EXECSTACK_TARGETS, normalize_execstack_targets

EXECSTACK_FILE_ACTIONS = frozenset(
    {
        "deploy",
        "update",
        "validate",
        "install_metamod",
        "update_metamod",
        "install_counterstrikesharp",
        "update_counterstrikesharp",
        "install_cs2fixes",
        "update_cs2fixes",
        "install_swiftly",
        "update_swiftly",
    }
)
EXECSTACK_FRAMEWORK_ACTIONS = frozenset(
    action
    for action in EXECSTACK_FILE_ACTIONS
    if action.startswith("install_") or action.startswith("update_")
)


@dataclass(frozen=True, slots=True)
class LinuxRelease:
    """The small, non-secret subset of ``/etc/os-release`` we persist."""

    os_id: str
    version_id: str

    @property
    def needs_execstack_clear(self) -> bool:
        """glibc 2.41 based Ubuntu/Debian releases need the compatibility fix."""
        try:
            major = int(self.version_id.split(".", 1)[0])
        except TypeError, ValueError:
            return False
        return (self.os_id == "debian" and major >= 13) or (self.os_id == "ubuntu" and major >= 25)


def effective_clear_execstack(server: Any) -> bool:
    """Resolve the persisted operator choice, falling back to the OS default."""
    override = getattr(server, "clear_execstack_override", None)
    if override is not None:
        return bool(override)
    os_id = getattr(server, "os_id", None)
    os_version = getattr(server, "os_version", None)
    if not os_id or not os_version:
        return False
    return LinuxRelease(str(os_id).lower(), str(os_version)).needs_execstack_clear


def execstack_cleanup_enabled_for_action(server: Any, action: str) -> bool:
    """Resolve the captured trigger policy for one operation."""
    if not effective_clear_execstack(server):
        return False
    if action == "restart":
        return bool(getattr(server, "execstack_fix_on_restart", True))
    if action in EXECSTACK_FRAMEWORK_ACTIONS:
        return bool(getattr(server, "execstack_fix_on_framework", True))
    if action in {"deploy", "update", "validate"}:
        return bool(getattr(server, "execstack_fix_on_game_update", False))
    return False


def execstack_operation_metadata(server: Any, action: str) -> dict[str, object] | None:
    """Capture the file-operation policy before a queued job starts."""
    if action not in EXECSTACK_FILE_ACTIONS:
        return None
    enabled = execstack_cleanup_enabled_for_action(server, action)
    metadata: dict[str, object] = {"clear_execstack": enabled}
    if enabled:
        targets = normalize_execstack_targets(
            getattr(server, "execstack_fix_targets", DEFAULT_EXECSTACK_TARGETS)
        )
        metadata["clear_execstack_command"] = build_clear_execstack_command(
            server.game_directory, targets
        )
        metadata["clear_execstack_targets"] = list(targets)
    return metadata


def execstack_targets_metadata(server: Any) -> dict[str, object]:
    """Capture the configured target paths and command for a queued restart."""
    targets = normalize_execstack_targets(
        getattr(server, "execstack_fix_targets", DEFAULT_EXECSTACK_TARGETS)
    )
    return {
        "clear_execstack_command": build_clear_execstack_command(server.game_directory, targets),
        "clear_execstack_targets": list(targets),
    }


def parse_linux_release(text: str) -> LinuxRelease | None:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip().strip('"').strip("'")
    os_id = (values.get("ID") or "").lower()
    if os_id not in {"debian", "ubuntu"}:
        return None
    version = values.get("VERSION_ID", "").strip()
    if not version:
        return None
    return LinuxRelease(os_id=os_id, version_id=version)


def execstack_addons_path(game_directory: str) -> str:
    raw = str(game_directory or "").strip()
    if not raw or raw == "/" or any(part == ".." for part in raw.split("/")):
        raise ValueError("Invalid game directory")
    root = posixpath.normpath(raw)
    return posixpath.join(root, "cs2", "game", "csgo", "addons")


def _target_path(game_directory: str, target: str) -> str:
    raw_target = str(target or "").strip()
    if (
        not raw_target
        or raw_target.startswith("/")
        or any(part == ".." for part in raw_target.split("/"))
    ):
        raise ValueError("Invalid execstack target")
    return posixpath.join(execstack_addons_path(game_directory), raw_target)


def build_clear_execstack_command(
    game_directory: str, targets: Any = DEFAULT_EXECSTACK_TARGETS
) -> str:
    addons = execstack_addons_path(game_directory)
    selected = normalize_execstack_targets(targets)
    paths = tuple(_target_path(game_directory, target) for target in selected)
    checks = "; ".join(
        f"if [ -f {quoted} ] && [ ! -L {quoted} ]; then patchelf --clear-execstack {quoted}; "
        f"else echo 'target {quoted} missing or symlink; skipped'; fi"
        for quoted in (shlex.quote(path) for path in paths)
    )
    return (
        "if command -v patchelf >/dev/null 2>&1 && "
        "patchelf --help 2>&1 | grep -q -- '--clear-execstack'; then "
        f"if [ -d {shlex.quote(addons)} ]; then {checks}; "
        "else echo 'addons directory missing; skipped'; fi; "
        "else echo 'patchelf --clear-execstack is unavailable; skipped' >&2; exit 2; fi"
    )


async def run_clear_execstack(manager: Any, server: Any, targets: Any = None) -> tuple[bool, str]:
    """Run the cleanup through an SSH manager and always close that connection."""
    try:
        connected, message = await manager.connect(server)
        if not connected:
            return False, message
        try:
            return await execute_clear_execstack_on_manager(manager, server, targets)
        finally:
            await manager.disconnect()
    except Exception as exc:
        return False, str(exc)


async def execute_clear_execstack_on_manager(
    manager: Any, server: Any, targets: Any = None
) -> tuple[bool, str]:
    """Run the cleanup on an already-connected manager without changing its session."""
    try:
        ok, stdout, stderr = await manager.execute_sudo_command(
            build_clear_execstack_command(
                server.game_directory,
                normalize_execstack_targets(
                    targets
                    if targets is not None
                    else getattr(server, "execstack_fix_targets", DEFAULT_EXECSTACK_TARGETS)
                ),
            ),
            getattr(server, "sudo_password", None),
            timeout=180,
        )
        return bool(ok), (stderr or stdout or "no output").strip()[-500:]
    except Exception as exc:
        return False, str(exc)


async def maybe_clear_execstack_after_file_action(
    *,
    server_id: int,
    action: str,
    server: Any,
    manager: Any,
    enabled: bool,
    targets: Any = None,
    report: Callable[[int, str, str], Awaitable[None]],
) -> None:
    """Apply the fix once after a file operation when the server is stopped."""
    if action not in EXECSTACK_FILE_ACTIONS or action in {"update", "validate"} or not enabled:
        return
    running = getattr(getattr(server, "status", None), "value", getattr(server, "status", None))
    if running == "running":
        await report(
            server_id,
            "output",
            "⚠ Plugin execstack cleanup deferred because the server is still running; use Operations > Restart.",
        )
        return
    await report(
        server_id,
        "output",
        "Clearing executable-stack flags from configured plugin targets after file operation...",
    )
    fixed, detail = await run_clear_execstack(manager, server, targets)
    message = (
        f"✓ Plugin execstack cleanup completed: {detail}"
        if fixed
        else f"⚠ Plugin execstack cleanup failed; continuing operation: {detail}"
    )
    await report(server_id, "output", message)


__all__ = [
    "LinuxRelease",
    "EXECSTACK_FILE_ACTIONS",
    "EXECSTACK_FRAMEWORK_ACTIONS",
    "DEFAULT_EXECSTACK_TARGETS",
    "build_clear_execstack_command",
    "effective_clear_execstack",
    "execstack_cleanup_enabled_for_action",
    "execstack_operation_metadata",
    "execstack_targets_metadata",
    "execute_clear_execstack_on_manager",
    "execstack_addons_path",
    "maybe_clear_execstack_after_file_action",
    "normalize_execstack_targets",
    "parse_linux_release",
    "run_clear_execstack",
]
