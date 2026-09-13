"""Remote path safety and extract-path helpers."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import status

from services.compat import LateBoundModule

host = LateBoundModule("api.routes.file_manager.common")


def is_path_safe(base_path: str, requested_path: str) -> bool:
    """
    Verify that the requested path is within the server's directory
    Prevents path traversal attacks
    """
    # Remote game servers use POSIX paths even when the panel runs on Windows.
    base = host.posixpath.normpath(base_path)
    requested = host.posixpath.normpath(requested_path)

    # Check if requested path is the base path or a child of it
    return requested == base or requested.startswith(base.rstrip("/") + "/")


def remote_join(*parts: str) -> str:
    """Join remote server paths using POSIX separators."""
    return host.posixpath.normpath(host.posixpath.join(*parts))


def safe_relative_upload_path(relative_path: str | None, filename: str | None) -> str:
    """Accept ``a/b/c.cfg`` for folder uploads. Reject ``..`` and absolute paths."""

    raw = (relative_path or "").strip().replace("\\", "/")
    fallback = host._validate_direct_child_name(filename or "upload", "filename")
    if not raw:
        return fallback
    if raw.startswith("/") or raw.startswith("./../") or raw.startswith("../"):
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="relative_path must stay inside the destination folder",
        )
    parts = [part for part in raw.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="relative_path must stay inside the destination folder",
        )
    return "/".join(host._validate_direct_child_name(part, "relative_path") for part in parts)


def _validate_direct_child_name(name: str, label: str = "name") -> str:
    """Validate a single remote path component without changing its value."""
    if not isinstance(name, str) or not name or not name.strip():
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{label} cannot be empty"
        )
    if name in (".", "..") or host.posixpath.basename(name) != name or "/" in name or "\\" in name:
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{label} must be a direct child name and cannot contain path separators",
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{label} cannot contain control characters",
        )
    if len(name.encode("utf-8")) > host.REMOTE_NAME_MAX_BYTES:
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{label} is too long (maximum {host.REMOTE_NAME_MAX_BYTES} UTF-8 bytes)",
        )
    return name


def _normalize_source_folder(source_folder: Optional[str]) -> Optional[str]:
    """Normalize and validate a directory name stored inside an archive."""
    if source_folder is None or not source_folder.strip():
        return None

    value = source_folder.strip().rstrip("/")
    while value.startswith("./"):
        value = value[2:]

    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source_folder must be a safe relative POSIX directory path",
        )

    normalized = host.posixpath.normpath(value)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source_folder cannot escape the archive root",
        )
    return normalized


_HUB_FILE_STATUS = {
    "queued": "pending",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
}


def resolve_extract_paths(server, archive_path: str, destination_path: str | None):
    """Normalize extract paths and reject anything outside the game directory."""
    archive = host.posixpath.normpath(archive_path)
    if not destination_path or not str(destination_path).strip():
        destination = host.posixpath.dirname(archive)
    else:
        destination = host.posixpath.normpath(destination_path)
    if not host.is_path_safe(server.game_directory, archive):
        raise host.HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: archive path is outside server directory",
        )
    if not host.is_path_safe(server.game_directory, destination):
        raise host.HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination path is outside server directory",
        )
    return archive, destination


def file_task_payload_from_hub(record: dict[str, Any]) -> dict[str, Any]:
    """Map a hub operation record onto the legacy FileTask poll shape."""
    status = host._HUB_FILE_STATUS.get(str(record.get("status") or ""), "pending")
    message = str(record["message"]) if record.get("message") else None
    destination = record.get("destination") or record.get("destination_path")
    target = record.get("target_path")
    return {
        "task_id": str(record["operation_id"]),
        "status": status,
        "message": None if status == "failed" else message,
        "error": message if status == "failed" else None,
        "target_path": str(target) if target else None,
        "destination": str(destination) if destination else None,
        "destination_path": str(destination) if destination else None,
        "archive_path": str(record["archive_path"]) if record.get("archive_path") else None,
        "elapsed_seconds": None,
    }
