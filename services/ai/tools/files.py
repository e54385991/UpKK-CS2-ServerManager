"""File, log, and configuration tools for the AI assistant."""

from __future__ import annotations

import hashlib
import posixpath
import re
import shlex
from typing import Any

from modules.models import Server
from modules.utils import get_current_time
from services.ai.tools.context import (
    ToolContext,
    _safe_relative_path,
    tools,
)
from services.ai.tools.schemas import (
    CSSLogListInput,
    CSSLogReadInput,
    FilePatchInput,
    FileReadInput,
    FileSearchInput,
    GameConsoleReadInput,
    TailLogInput,
)
from services.ai_security import redact_sensitive_text, sanitize_tool_result


async def search_server_files(ctx: ToolContext, data: FileSearchInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    relative = _safe_relative_path(data.relative_path)
    path = posixpath.join(server.game_directory.rstrip("/"), relative)
    await ctx.emit(
        "tool_progress",
        {"message": f"Searching for '{data.query}' in {relative or '.'}"},
    )
    manager = await tools._connect(server)
    try:
        valid, error = await manager.validate_path_within_base(
            server.game_directory, path, server, allow_missing=True
        )
        if not valid:
            raise ValueError(error)
        safe_path = shlex.quote(path)
        safe_query = shlex.quote(data.query)
        if data.search_content:
            command = (
                f"test -d {safe_path} && "
                f"find {safe_path} -xdev -type f -size -1M -print0 2>/dev/null | "
                f"xargs -0 -r grep -Il -- {safe_query} 2>/dev/null | head -n {data.limit}"
            )
        else:
            pattern = shlex.quote(f"*{data.query}*")
            command = (
                f"test -d {safe_path} && "
                f"find {safe_path} -xdev \\( -type f -o -type d \\) -iname {pattern} "
                f"-print 2>/dev/null | head -n {data.limit}"
            )
        success, stdout, stderr = await manager.execute_command(command, timeout=30)
        if not success:
            raise RuntimeError(stderr or stdout or "File search failed")
    finally:
        await manager.disconnect()
    prefix = server.game_directory.rstrip("/") + "/"
    paths = [line.strip().removeprefix(prefix) for line in stdout.splitlines() if line.strip()]
    result: dict[str, Any] = {
        "matches": paths,
        "count": len(paths),
        "truncated": len(paths) >= data.limit,
    }
    if not paths:
        result["note"] = (
            f"No files matching '{data.query}' found in {relative or '.'}. This is normal if the path does not exist or has no matches."
        )
    return result


async def read_server_text_file(ctx: ToolContext, data: FileReadInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    relative = _safe_relative_path(data.relative_path)
    path = posixpath.join(server.game_directory.rstrip("/"), relative)
    manager = await tools._connect(server)
    try:
        valid, error = await manager.validate_path_within_base(
            server.game_directory,
            path,
            server,
            allow_missing=False,
            require_regular=True,
        )
        if not valid:
            raise ValueError(error)
        success, content, error = await manager.read_file(path, server, max_size=256_000)
        if not success:
            raise RuntimeError(error)
    finally:
        await manager.disconnect()
    if "\x00" in content:
        raise ValueError("Binary files cannot be sent to the AI provider")
    if len(content.encode("utf-8")) > 256_000:
        raise ValueError("File exceeds the 256 KB AI read limit")
    return {
        "path": relative,
        "revision": hashlib.sha256(content.encode()).hexdigest(),
        "content": redact_sensitive_text(content),
    }


async def tail_server_log(ctx: ToolContext, data: TailLogInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    path = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo/console.log")
    manager = await tools._connect(server)
    try:
        valid, error = await manager.validate_path_within_base(
            server.game_directory, path, server, allow_missing=True, require_regular=False
        )
        if not valid:
            raise ValueError(error)
        success, stdout, stderr = await manager.execute_command(
            f"test -f {shlex.quote(path)} && tail -n {data.lines} -- {shlex.quote(path)}",
            timeout=20,
        )
        if not success:
            raise RuntimeError(stderr or stdout or "Unable to read console.log")
    finally:
        await manager.disconnect()
    if not stdout.strip():
        return {
            "path": "cs2/game/csgo/console.log",
            "content": "",
            "note": "console.log does not exist yet. The server may not have been started, "
            "or the log was cleaned up. Start the server first to generate logs.",
        }
    return {"path": "cs2/game/csgo/console.log", "content": redact_sensitive_text(stdout)}


async def read_game_console(ctx: ToolContext, data: GameConsoleReadInput) -> dict[str, Any]:
    """Read the live detached game console without sending any input."""

    server = await tools._require_current_server(ctx)
    from services.custom_command_service import read_game_console as read_console

    return sanitize_tool_result(await read_console(server, lines=data.lines))


def _css_log_root(server: Server) -> str:
    return posixpath.join(
        server.game_directory.rstrip("/"),
        "cs2/game/csgo/addons/counterstrikesharp/logs",
    )


def _safe_css_log_name(value: str) -> str:
    if (
        re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None
        or value.startswith(".")
        or not value.casefold().endswith((".log", ".txt"))
    ):
        raise ValueError("Select a CounterStrikeSharp log returned by list_css_error_logs")
    return value


async def list_css_error_logs(ctx: ToolContext, data: CSSLogListInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    await tools.enforce_agent_rate_limit(ctx.user.id, "css_log_list", limit=20)
    root = _css_log_root(server)
    manager = await tools._connect(server)
    try:
        valid, error = await manager.validate_path_within_base(
            server.game_directory, root, server, allow_missing=False
        )
        if not valid:
            raise ValueError(error)
        command = (
            f"find {shlex.quote(root)} -xdev -maxdepth 1 -type f "
            "\\( -name '*.log' -o -name '*.txt' \\) "
            "-printf '%T@\\t%f\\t%s\\n' 2>/dev/null | sort -rn | head -n 50"
        )
        success, stdout, stderr = await manager.execute_command(command, timeout=20)
        if not success:
            raise RuntimeError(stderr or stdout or "Unable to list CounterStrikeSharp logs")
        logs: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            modified, raw_name, raw_size = parts
            try:
                name = _safe_css_log_name(raw_name)
                size = int(raw_size)
            except ValueError, TypeError:
                continue
            log_path = posixpath.join(root, name)
            text_file, _, _ = await manager.execute_command(
                f"if test -s {shlex.quote(log_path)}; then "
                f"tail -c 8192 -- {shlex.quote(log_path)} | grep -Iq .; fi",
                timeout=10,
            )
            if not text_file:
                continue
            if data.keyword:
                matched, _, _ = await manager.execute_command(
                    f"grep -Iqi -- {shlex.quote(data.keyword)} {shlex.quote(log_path)}",
                    timeout=10,
                )
                if not matched:
                    continue
            logs.append(
                {
                    "name": name,
                    "size": size,
                    "modified_epoch": modified,
                    "read_returns_tail_only": size > 256 * 1024,
                }
            )
            if len(logs) >= data.limit:
                break
    finally:
        await manager.disconnect()
    return {
        "directory": "cs2/game/csgo/addons/counterstrikesharp/logs",
        "logs": logs,
        "keyword": data.keyword,
        "untrusted_content": True,
    }


async def read_css_error_log(ctx: ToolContext, data: CSSLogReadInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    await tools.enforce_agent_rate_limit(ctx.user.id, "css_log_read", limit=20)
    name = _safe_css_log_name(data.log_name)
    path = posixpath.join(_css_log_root(server), name)
    console_path = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo/console.log")
    manager = await tools._connect(server)
    try:
        valid, error = await manager.validate_path_within_base(
            server.game_directory,
            path,
            server,
            allow_missing=False,
            require_regular=True,
        )
        if not valid:
            raise ValueError(error)
        text_check, _, _ = await manager.execute_command(
            f"if test -s {shlex.quote(path)}; then "
            f"tail -c 262144 -- {shlex.quote(path)} | grep -Iq .; fi",
            timeout=15,
        )
        if not text_check:
            raise ValueError("CounterStrikeSharp log is binary or unreadable")
        if data.keyword:
            read_command = (
                f"tail -c 262144 -- {shlex.quote(path)} | "
                f"grep -i -C 4 -- {shlex.quote(data.keyword)} | tail -n {data.lines}"
            )
        else:
            read_command = f"tail -c 262144 -- {shlex.quote(path)} | tail -n {data.lines}"
        _ok, content, _read_error = await manager.execute_command(read_command, timeout=20)
        _console_ok, console_tail, _ = await manager.execute_command(
            f"tail -n 200 -- {shlex.quote(console_path)} 2>/dev/null", timeout=15
        )
        process_pattern = posixpath.join(
            server.game_directory.rstrip("/"), "cs2/game/bin/linuxsteamrt64/cs2"
        )
        evidence_command = (
            f"printf 'processes='; pgrep -f -- {shlex.quote(process_pattern)} "
            "2>/dev/null | wc -l; "
            f"if ss -lunt 2>/dev/null | grep -Eq -- {shlex.quote(f':{server.game_port}([[:space:]]|$)')}; "
            "then printf 'port_listening=yes\\n'; else printf 'port_listening=no\\n'; fi"
        )
        _evidence_ok, process_evidence, _ = await manager.execute_command(
            evidence_command, timeout=15
        )
        correlation_pattern = data.keyword or "error|exception|fatal|crash"
        correlation_flag = "-iFl" if data.keyword else "-iEl"
        _related_ok, related_output, _ = await manager.execute_command(
            f"find {shlex.quote(_css_log_root(server))} -xdev -maxdepth 1 -type f "
            "\\( -name '*.log' -o -name '*.txt' \\) -exec "
            f"grep {correlation_flag} -- {shlex.quote(correlation_pattern)} {{}} + "
            "2>/dev/null | head -n 10",
            timeout=20,
        )
    finally:
        await manager.disconnect()
    from services.a2s_query import a2s_service

    a2s_ok, a2s_info = await a2s_service.query_server_info(
        server.a2s_query_host or server.host,
        server.a2s_query_port or server.game_port,
        timeout=5.0,
    )
    return {
        "path": f"cs2/game/csgo/addons/counterstrikesharp/logs/{name}",
        "content": redact_sensitive_text(content),
        "console_log_tail": redact_sensitive_text(console_tail, limit=8000),
        "process_and_port": process_evidence,
        "a2s": {"reachable": a2s_ok, "info": a2s_info},
        "related_logs": [
            safe_name
            for item in related_output.splitlines()
            if (safe_name := posixpath.basename(item)) != name
            and re.fullmatch(r"[A-Za-z0-9_.-]+", safe_name)
        ][:10],
        "untrusted_content": True,
    }


async def patch_server_text_file(ctx: ToolContext, data: FilePatchInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    relative = _safe_relative_path(data.relative_path)
    allowed_extensions = (
        ".cfg",
        ".txt",
        ".json",
        ".jsonc",
        ".ini",
        ".yaml",
        ".yml",
        ".toml",
        ".vdf",
        ".sp",
        ".cs",
        ".conf",
        ".xml",
        ".sh",
        ".env",
        ".list",
        ".nut",
    )
    if not relative.lower().endswith(allowed_extensions):
        raise ValueError("AI edits are restricted to recognized text configuration files")
    if "\x00" in data.content:
        raise ValueError("Configuration content cannot contain null bytes")
    path = posixpath.join(server.game_directory.rstrip("/"), relative)
    manager = await tools._connect(server)
    try:
        valid, error = await manager.validate_path_within_base(
            server.game_directory,
            path,
            server,
            allow_missing=False,
            require_regular=True,
        )
        if not valid:
            raise ValueError(error)
        success, current, error = await manager.read_file(path, server)
        if not success:
            raise RuntimeError(error)
        revision = hashlib.sha256(current.encode()).hexdigest()
        if revision != data.expected_revision:
            raise ValueError(
                "File changed since it was read; inspect the current revision before retrying"
            )
        backup = f"{path}.ai-backup-{get_current_time().strftime('%Y%m%d%H%M%S')}"
        success, stdout, stderr = await manager.execute_command(
            f"cp -p -- {shlex.quote(path)} {shlex.quote(backup)}", timeout=20
        )
        if not success:
            raise RuntimeError(stderr or stdout or "Unable to create configuration backup")
        success, error = await manager.write_file(path, data.content, server)
        if not success:
            raise RuntimeError(error)
    finally:
        await manager.disconnect()
    from services.audit_log_service import record_audit_event

    await record_audit_event(
        category="files",
        action="files.edit",
        status="success",
        user=ctx.user,
        source="assistant",
        server_id=server.id,
        details={"path": relative, "bytes": len(data.content.encode()), "source": "assistant"},
    )
    return {
        "success": True,
        "path": relative,
        "backup_path": backup.removeprefix(server.game_directory.rstrip("/") + "/"),
        "revision": hashlib.sha256(data.content.encode()).hexdigest(),
    }
