"""Delete selected plugin files from a game server over SSH."""

from __future__ import annotations

import logging
import shlex
from collections.abc import Awaitable, Callable

from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)

Progress = Callable[[str, str], Awaitable[None]]


def validate_uninstall_path(file_path: str) -> str:
    """Return a relative csgo path, or raise ValueError on traversal."""
    text = str(file_path).replace("\\", "/").strip()
    if not text:
        raise ValueError("File path cannot be empty")
    if "\x00" in text:
        raise ValueError("File paths cannot contain null bytes")
    if text.startswith("/"):
        raise ValueError("File paths must be relative (cannot start with /)")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("File paths cannot contain path traversal sequences (..)")
    return "/".join(parts)


def normalize_uninstall_paths(files_to_delete: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in files_to_delete:
        path = validate_uninstall_path(raw)
        if path in seen:
            continue
        seen.add(path)
        cleaned.append(path)
    if not cleaned:
        raise ValueError("Select at least one file to delete")
    return cleaned


async def uninstall_plugin_files(
    *,
    server,
    files_to_delete: list[str],
    progress: Progress | None = None,
) -> dict:
    """Remove validated relative paths under the server csgo directory."""
    cleaned = normalize_uninstall_paths(files_to_delete)

    async def emit(message: str, kind: str = "status") -> None:
        if progress is not None:
            await progress(message, kind)

    ssh_manager = SSHManager()
    success, msg = await ssh_manager.connect(server)
    if not success:
        await emit(f"SSH connection failed: {msg}", "error")
        return {
            "success": False,
            "message": f"SSH connection failed: {msg}",
            "deleted_files": 0,
            "failed_files": cleaned,
        }

    try:
        await emit("Starting plugin uninstallation...")
        csgo_dir = f"{server.game_directory.rstrip('/')}/cs2/game/csgo"
        deleted_count = 0
        failed_files: list[str] = []
        for file_path in cleaned:
            full_path = f"{csgo_dir}/{file_path}"
            delete_cmd = f"rm -rf -- {shlex.quote(full_path)}"
            ok, _, stderr = await ssh_manager.execute_command(delete_cmd)
            if ok:
                deleted_count += 1
                await emit(f"Deleted: {file_path}")
            else:
                failed_files.append(file_path)
                await emit(f"Failed to delete: {file_path} - {stderr}", "warning")

        if failed_files:
            message = (
                f"Uninstallation completed with errors. Deleted {deleted_count} files, "
                f"failed {len(failed_files)} files."
            )
            await emit(message, "warning")
        else:
            message = f"Successfully uninstalled plugin. Deleted {deleted_count} files."
            await emit(message, "complete")

        return {
            "success": len(failed_files) == 0,
            "message": message,
            "deleted_files": deleted_count,
            "failed_files": failed_files,
        }
    except Exception as exc:
        logger.exception("Plugin file uninstall failed")
        error_msg = f"Uninstallation error: {exc}"
        await emit(error_msg, "error")
        return {
            "success": False,
            "message": error_msg,
            "deleted_files": 0,
            "failed_files": cleaned,
        }
    finally:
        await ssh_manager.disconnect()
