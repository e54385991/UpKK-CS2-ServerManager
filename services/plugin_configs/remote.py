"""SSH/SFTP access for plugin configuration sources."""

from __future__ import annotations

import asyncio
import contextlib
import posixpath
import shlex
import uuid
from typing import Any, AsyncIterator, Optional

from asyncssh.constants import (
    FILEXFER_TYPE_DIRECTORY,
    FILEXFER_TYPE_REGULAR,
    FILEXFER_TYPE_SYMLINK,
)

from modules import Server
from services.ssh_manager import SSHManager

from .parser import (
    MAX_CONFIG_BYTES,
    SUPPORTED_DIRECTORY_EXTENSIONS,
    PluginConfigError,
    format_for_filename,
)

MAX_SOURCE_FILES = 2000
SCAN_READ_BYTES = 64 * 1024
SCAN_MAX_TOKEN_BYTES = 64 * 1024
SCAN_IDLE_TIMEOUT = 30
SCAN_STOP_TIMEOUT = 2
SCAN_ERROR_BYTES = 4096


def absolute_path(server: Server, relative_path: str) -> str:
    return posixpath.normpath(posixpath.join(server.game_directory, relative_path))


async def _sftp_root(
    ssh_manager: SSHManager,
    server: Server,
    relative_path: str,
) -> tuple[Any, str, Any]:
    target = absolute_path(server, relative_path)
    valid, error = await ssh_manager.validate_path_within_base(
        server.game_directory, target, server, require_regular=False
    )
    if not valid:
        raise PluginConfigError(error)
    conn = ssh_manager.conn
    if conn is None:
        raise PluginConfigError("SSH connection is not established")
    sftp = await conn.start_sftp_client()
    try:
        attrs = await sftp.lstat(target)
    except Exception:
        sftp.exit()
        await sftp.wait_closed()
        raise
    if attrs.type == FILEXFER_TYPE_SYMLINK:
        sftp.exit()
        await sftp.wait_closed()
        raise PluginConfigError("Symbolic links cannot be used as configuration sources")
    return sftp, target, attrs


async def inspect_source(
    ssh_manager: SSHManager,
    server: Server,
    relative_path: str,
) -> str:
    sftp, _, attrs = await _sftp_root(ssh_manager, server, relative_path)
    try:
        if attrs.type == FILEXFER_TYPE_DIRECTORY:
            return "directory"
        if attrs.type == FILEXFER_TYPE_REGULAR:
            return "file"
        raise PluginConfigError("Configuration source must be a regular file or directory")
    finally:
        sftp.exit()
        await sftp.wait_closed()


async def browse_directory(
    ssh_manager: SSHManager,
    server: Server,
    relative_path: str,
) -> list[dict[str, Any]]:
    sftp, target, attrs = await _sftp_root(ssh_manager, server, relative_path)
    try:
        if attrs.type != FILEXFER_TYPE_DIRECTORY:
            raise PluginConfigError("Browse path is not a directory")
        items: list[dict[str, Any]] = []
        async for entry in sftp.scandir(target):
            if entry.filename in {".", ".."}:
                continue
            entry_type = entry.attrs.type
            if entry_type == FILEXFER_TYPE_SYMLINK:
                items.append({"name": entry.filename, "type": "symlink", "selectable": False})
                continue
            if entry_type not in {
                FILEXFER_TYPE_DIRECTORY,
                FILEXFER_TYPE_REGULAR,
            }:
                continue
            child_relative = posixpath.normpath(posixpath.join(relative_path, entry.filename))
            items.append(
                {
                    "name": entry.filename,
                    "path": child_relative,
                    "type": "directory" if entry_type == FILEXFER_TYPE_DIRECTORY else "file",
                    "selectable": True,
                    "size": entry.attrs.size or 0,
                }
            )
        return sorted(items, key=lambda item: (item["type"] != "directory", item["name"].lower()))
    finally:
        sftp.exit()
        await sftp.wait_closed()


def _source_kind(file_type: int) -> str:
    if file_type == FILEXFER_TYPE_DIRECTORY:
        return "directory"
    if file_type == FILEXFER_TYPE_REGULAR:
        return "file"
    return "unsupported"


def _decode_scan_token(raw_token: bytes) -> str:
    try:
        value = raw_token.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PluginConfigError("Configuration source contains a non-UTF-8 path") from exc
    if "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PluginConfigError("Configuration source contains an unsafe path")
    return value


def _append_scan_token(
    raw_token: bytes,
    record_type: Optional[str],
    record: list[str],
) -> tuple[Optional[str], list[str], Optional[tuple[str, list[str]]]]:
    """Append one NUL-delimited token and return a completed record when ready."""
    token = _decode_scan_token(raw_token)
    if record_type is None:
        if token not in {"D", "F"}:
            raise PluginConfigError("Remote scan returned an invalid record")
        return token, [], None

    next_record = [*record, token]
    required = 1 if record_type == "D" else 3
    if len(next_record) < required:
        return record_type, next_record, None
    return None, [], (record_type, next_record)


def _scan_record_event(
    record_type: str,
    values: list[str],
    relative_path: str,
    count: int,
) -> tuple[dict[str, Any], int]:
    tree_path = values[0]
    if record_type == "D":
        return {"type": "progress", "directory": tree_path or ".", "count": count}, count
    try:
        size = int(values[1])
        modified = float(values[2])
    except ValueError as exc:
        raise PluginConfigError("Remote scan returned invalid file metadata") from exc
    game_relative = posixpath.normpath(posixpath.join(relative_path, tree_path))
    next_count = count + 1
    return {
        "type": "file",
        "file": {
            "name": posixpath.basename(tree_path),
            "path": game_relative,
            "tree_path": tree_path,
            "size": size,
            "modified": modified,
            "format": format_for_filename(tree_path),
            "too_large": size > MAX_CONFIG_BYTES,
        },
    }, next_count


def _scan_command(target: str) -> str:
    extension_expression = " -o ".join(
        f"-iname {shlex.quote('*' + extension)}"
        for extension in sorted(SUPPORTED_DIRECTORY_EXTENSIONS)
    )
    return (
        f"LC_ALL=C find -P {shlex.quote(target)} "
        r"\( -type d -printf 'D\0%P\0' \) -o "
        rf"\( -type f \( {extension_expression} \) "
        r"-printf 'F\0%P\0%s\0%T@\0' \)"
    )


async def _stop_scan_process(process: Any) -> None:
    with contextlib.suppress(Exception):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=SCAN_STOP_TIMEOUT)
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            process.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=SCAN_STOP_TIMEOUT)
    except Exception:
        pass


async def _read_scan_stderr(process: Any) -> str:
    retained = bytearray()
    while True:
        chunk = await process.stderr.read(SCAN_READ_BYTES)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", errors="replace")
        remaining = SCAN_ERROR_BYTES - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
    return retained.decode("utf-8", errors="replace").strip()


async def _iter_directory_scan(
    process: Any,
    relative_path: str,
    stop_process,
) -> AsyncIterator[dict[str, Any]]:
    """Decode the bounded NUL-delimited output emitted by the remote find."""
    buffer = bytearray()
    record_type: Optional[str] = None
    record: list[str] = []
    count = 0
    truncated = False

    while not truncated:
        try:
            chunk = await asyncio.wait_for(
                process.stdout.read(SCAN_READ_BYTES),
                timeout=SCAN_IDLE_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise PluginConfigError("Remote configuration scan timed out") from exc
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        buffer.extend(chunk)
        while True:
            delimiter = buffer.find(b"\0")
            if delimiter < 0:
                break
            raw_token = bytes(buffer[:delimiter])
            del buffer[: delimiter + 1]
            record_type, record, completed = _append_scan_token(raw_token, record_type, record)
            if completed is None:
                continue
            if completed[0] == "F" and count >= MAX_SOURCE_FILES:
                truncated = True
                await stop_process()
                break
            event, count = _scan_record_event(*completed, relative_path, count)
            yield event
        if len(buffer) > SCAN_MAX_TOKEN_BYTES:
            raise PluginConfigError("Configuration source path is too long")

    if not truncated:
        if buffer or record_type is not None:
            raise PluginConfigError("Remote scan returned an incomplete record")
        await asyncio.wait_for(process.wait(), timeout=SCAN_IDLE_TIMEOUT)
    yield {"type": "complete", "truncated": truncated, "count": count}


async def iter_source_scan(
    ssh_manager: SSHManager,
    server: Server,
    relative_path: str,
    source_type: str,
) -> AsyncIterator[dict[str, Any]]:
    """Yield matching files from one non-following remote ``find`` process."""
    sftp, target, attrs = await _sftp_root(ssh_manager, server, relative_path)
    process = None
    process_finished = False
    stderr_task = None

    try:
        if _source_kind(attrs.type) != source_type:
            raise PluginConfigError("Configuration source type changed on the remote server")
        if source_type == "file":
            yield {
                "type": "file",
                "file": {
                    "name": posixpath.basename(target),
                    "path": relative_path,
                    "tree_path": posixpath.basename(target),
                    "size": attrs.size or 0,
                    "modified": attrs.mtime or 0,
                    "format": format_for_filename(target),
                    "too_large": (attrs.size or 0) > MAX_CONFIG_BYTES,
                },
            }
        else:
            # Close the validation channel before starting the single remote traversal.
            sftp.exit()
            await sftp.wait_closed()
            sftp = None
            conn = ssh_manager.conn
            if conn is None:
                raise PluginConfigError("SSH connection is not established")
            process = await conn.create_process(_scan_command(target), encoding=None)
            stderr_task = asyncio.create_task(_read_scan_stderr(process))
            async for event in _iter_directory_scan(
                process,
                relative_path,
                lambda: _stop_scan_process(process),
            ):
                yield event
            process_finished = True
            stderr = await stderr_task
            if process.exit_status != 0:
                raise PluginConfigError(stderr or "Remote configuration scan failed")
    finally:
        if process is not None and not process_finished:
            await _stop_scan_process(process)
        if stderr_task is not None:
            if not stderr_task.done():
                stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await stderr_task
        if sftp is not None:
            sftp.exit()
            await sftp.wait_closed()


async def scan_source(
    ssh_manager: SSHManager,
    server: Server,
    relative_path: str,
    source_type: str,
) -> dict[str, Any]:
    """Collect the streaming scanner for non-streaming callers and tests."""
    files: list[dict[str, Any]] = []
    complete: dict[str, Any] = {"truncated": False, "count": 0}
    async for event in iter_source_scan(ssh_manager, server, relative_path, source_type):
        if event["type"] == "file":
            files.append(event["file"])
        elif event["type"] == "complete":
            complete = event
    files.sort(key=lambda item: item["tree_path"].lower())
    return {
        "files": files,
        "truncated": bool(complete["truncated"]),
        "count": int(complete["count"]),
    }


async def read_text_file(
    ssh_manager: SSHManager,
    server: Server,
    relative_path: str,
) -> str:
    target = absolute_path(server, relative_path)
    valid, error = await ssh_manager.validate_path_within_base(
        server.game_directory, target, server, require_regular=True
    )
    if not valid:
        raise PluginConfigError(error)
    conn = ssh_manager.conn
    if conn is None:
        raise PluginConfigError("SSH connection is not established")
    async with conn.start_sftp_client() as sftp:
        attrs = await sftp.lstat(target)
        if (attrs.size or 0) > MAX_CONFIG_BYTES:
            raise PluginConfigError("Configuration exceeds the 10 MiB size limit")
        async with sftp.open(target, "rb") as remote_file:
            data = await remote_file.read(MAX_CONFIG_BYTES + 1)
    if len(data) > MAX_CONFIG_BYTES:
        raise PluginConfigError("Configuration exceeds the 10 MiB size limit")
    if b"\x00" in data:
        raise PluginConfigError("Binary files cannot be edited as configuration text")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginConfigError("Configuration must be UTF-8 text") from exc


async def atomic_write_text_file(
    ssh_manager: SSHManager,
    server: Server,
    relative_path: str,
    content: str,
) -> None:
    target = absolute_path(server, relative_path)
    valid, error = await ssh_manager.validate_path_within_base(
        server.game_directory, target, server, require_regular=True
    )
    if not valid:
        raise PluginConfigError(error)
    temporary = f"{target}.upkk-{uuid.uuid4().hex}.tmp"
    conn = ssh_manager.conn
    if conn is None:
        raise PluginConfigError("SSH connection is not established")
    try:
        async with conn.start_sftp_client() as sftp:
            original_attrs = await sftp.lstat(target)
            async with sftp.open(temporary, "wb") as remote_file:
                await remote_file.write(content.encode("utf-8"))
            if original_attrs.permissions is not None:
                await sftp.chmod(temporary, original_attrs.permissions & 0o7777)
        quoted_temporary = shlex.quote(temporary)
        quoted_target = shlex.quote(target)
        success, stdout, stderr = await ssh_manager.execute_command(
            f"chown --reference={quoted_target} -- {quoted_temporary} 2>/dev/null || true; "
            f"mv -f -- {quoted_temporary} {quoted_target}",
            timeout=20,
        )
        if not success:
            raise PluginConfigError((stderr or stdout or "Atomic replace failed").strip())
    except Exception:
        await ssh_manager.execute_command(f"rm -f -- {shlex.quote(temporary)}", timeout=10)
        raise


__all__ = [
    "MAX_SOURCE_FILES",
    "SCAN_READ_BYTES",
    "SCAN_MAX_TOKEN_BYTES",
    "SCAN_IDLE_TIMEOUT",
    "SCAN_STOP_TIMEOUT",
    "SCAN_ERROR_BYTES",
    "absolute_path",
    "inspect_source",
    "browse_directory",
    "iter_source_scan",
    "scan_source",
    "read_text_file",
    "atomic_write_text_file",
]
