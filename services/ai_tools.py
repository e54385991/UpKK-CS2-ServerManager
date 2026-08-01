"""Typed, permission-checked tools exposed to the AI model."""

from __future__ import annotations

import difflib
import hashlib
import json
import posixpath
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import ManagedPlugin, MarketPlugin, PluginCategory, Server, ServerStatus, User
from modules.utils import get_current_time
from services.ai_knowledge import KNOWLEDGE_TOPICS, lookup_knowledge
from services.ai_security import redact_sensitive_text, sanitize_tool_result
from services.maintenance_lock import maintenance_lock_service
from services.ssh_manager import SSHManager


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(ToolInput):
    pass


class KnowledgeInput(ToolInput):
    topic: Literal[
        "layout",
        "deployment",
        "steamcmd",
        "startup",
        "logs_and_config",
        "metamod",
        "counterstrikesharp",
        "plugins",
        "workshop_maps",
    ]


class FileSearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(default=".", max_length=500)
    search_content: bool = False
    limit: int = Field(default=50, ge=1, le=100)


class FileReadInput(ToolInput):
    relative_path: str = Field(min_length=1, max_length=500)


class TailLogInput(ToolInput):
    lines: int = Field(default=120, ge=10, le=500)


class PluginSearchInput(ToolInput):
    query: str = Field(default="", max_length=128)
    category: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=10, ge=1, le=20)


class PluginPlanInput(ToolInput):
    plugin_id: int = Field(gt=0)


class WorkshopPlanInput(ToolInput):
    workshop_id_or_url: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    min_players: int = Field(default=0, ge=0, le=64)
    only_nominate: bool = False
    restricted_times: str = Field(default="", max_length=512)


class ServerOperationInput(ToolInput):
    operation: Literal["deploy", "update", "validate"]


class ServerControlInput(ToolInput):
    action: Literal["start", "stop", "restart"]


class FilePatchInput(ToolInput):
    relative_path: str = Field(min_length=1, max_length=500)
    expected_revision: str = Field(min_length=64, max_length=64)
    content: str = Field(max_length=256_000)


class ApplyPluginPlanInput(PluginPlanInput):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)


class ApplyWorkshopPlanInput(WorkshopPlanInput):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)


EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class ToolContext:
    db: AsyncSession
    user: User
    server: Server | None
    emit: EventEmitter


ToolHandler = Callable[[ToolContext, ToolInput], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    risk: Literal["read", "write", "destructive"]
    input_model: type[ToolInput]
    handler: ToolHandler
    requires_server: bool = True

    def api_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


async def _require_current_server(ctx: ToolContext) -> Server:
    if ctx.server is None or ctx.server.id is None:
        raise ValueError("Select a server before using this tool")
    if ctx.user.is_admin:
        server = await Server.get_by_id(ctx.db, ctx.server.id)
    else:
        server = await Server.get_by_id_and_user(ctx.db, ctx.server.id, ctx.user.id)
    if server is None:
        raise PermissionError("Server is no longer available to this user")
    return server


def _safe_relative_path(relative_path: str) -> str:
    value = relative_path.replace("\\", "/").strip()
    normalized = posixpath.normpath(value)
    if (
        not value
        or value.startswith("/")
        or normalized == ".."
        or normalized.startswith("../")
        or "\x00" in value
    ):
        raise ValueError("Path must remain inside the managed game directory")
    return normalized


async def _connect(server: Server) -> SSHManager:
    manager = SSHManager()
    success, message = await manager.connect(server)
    if not success:
        raise RuntimeError(f"SSH connection failed: {message}")
    return manager


async def list_servers(ctx: ToolContext, _: EmptyInput) -> dict[str, Any]:
    servers = (
        await Server.get_all(ctx.db, limit=100)
        if ctx.user.is_admin
        else await Server.get_all_by_user(ctx.db, ctx.user.id, limit=100)
    )
    return {
        "servers": [
            {
                "id": server.id,
                "name": server.name,
                "status": server.status.value if server.status else "unknown",
                "game_port": server.game_port,
                "game_directory": server.game_directory,
            }
            for server in servers
        ]
    }


async def inspect_server(ctx: ToolContext, _: EmptyInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    manager = await _connect(server)
    root = shlex.quote(server.game_directory.rstrip("/"))
    binary = shlex.quote(
        posixpath.join(server.game_directory.rstrip("/"), "cs2/game/bin/linuxsteamrt64/cs2")
    )
    try:
        command = (
            f"if test -x {binary}; then printf 'binary=ok\\n'; else printf 'binary=missing\\n'; fi; "
            f"printf 'processes='; pgrep -f -- {shlex.quote(server.game_directory + '/cs2/game/bin/linuxsteamrt64/cs2')} "
            "2>/dev/null | wc -l; "
            f"df -Pk -- {root} 2>/dev/null | tail -1 | "
            "awk '{printf \"disk_kb=%s\\ndisk_used_percent=%s\\n\", $4, $5}'"
        )
        success, stdout, stderr = await manager.execute_command(command, timeout=20)
        if not success:
            raise RuntimeError(stderr or stdout or "Inspection failed")
    finally:
        await manager.disconnect()
    facts = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            facts[key] = value
    from services.a2s_query import a2s_service

    query_host = server.a2s_query_host or server.host
    query_port = server.a2s_query_port or server.game_port
    a2s_success, a2s_info = await a2s_service.query_server_info(query_host, query_port, timeout=5.0)
    return {
        "server": {
            "id": server.id,
            "name": server.name,
            "panel_status": server.status.value if server.status else "unknown",
            "game_directory": server.game_directory,
            "game_port": server.game_port,
            "session_manager": server.session_manager,
            "ssh_health": server.ssh_health_status,
            "last_ssh_success": server.last_ssh_success,
        },
        "inspection": facts,
        "a2s": {
            "reachable": a2s_success,
            "query_host": query_host,
            "query_port": query_port,
            "info": a2s_info,
        },
    }


async def search_server_files(ctx: ToolContext, data: FileSearchInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    relative = _safe_relative_path(data.relative_path)
    path = posixpath.join(server.game_directory.rstrip("/"), relative)
    manager = await _connect(server)
    try:
        valid, error = await manager.validate_path_within_base(
            server.game_directory, path, server, allow_missing=False
        )
        if not valid:
            raise ValueError(error)
        safe_path = shlex.quote(path)
        safe_query = shlex.quote(data.query)
        if data.search_content:
            command = (
                f"find {safe_path} -xdev -type f -size -1M -print0 2>/dev/null | "
                f"xargs -0 -r grep -Il -- {safe_query} 2>/dev/null | head -n {data.limit}"
            )
        else:
            pattern = shlex.quote(f"*{data.query}*")
            command = (
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
    return {"matches": paths, "count": len(paths), "truncated": len(paths) >= data.limit}


async def read_server_text_file(ctx: ToolContext, data: FileReadInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    relative = _safe_relative_path(data.relative_path)
    path = posixpath.join(server.game_directory.rstrip("/"), relative)
    manager = await _connect(server)
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
    server = await _require_current_server(ctx)
    path = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo/console.log")
    manager = await _connect(server)
    try:
        valid, error = await manager.validate_path_within_base(
            server.game_directory, path, server, allow_missing=False, require_regular=True
        )
        if not valid:
            raise ValueError(error)
        success, stdout, stderr = await manager.execute_command(
            f"tail -n {data.lines} -- {shlex.quote(path)}", timeout=20
        )
        if not success:
            raise RuntimeError(stderr or stdout or "Unable to read console.log")
    finally:
        await manager.disconnect()
    return {"path": "cs2/game/csgo/console.log", "content": redact_sensitive_text(stdout)}


async def lookup_cs2_knowledge(ctx: ToolContext, data: KnowledgeInput) -> dict[str, Any]:
    del ctx
    return {"topic": data.topic, "content": lookup_knowledge(data.topic)}


async def search_plugin_market(ctx: ToolContext, data: PluginSearchInput) -> dict[str, Any]:
    try:
        category = PluginCategory(data.category) if data.category else None
    except ValueError as exc:
        raise ValueError("Unknown plugin category") from exc
    plugins, total = await MarketPlugin.search_plugins(
        ctx.db,
        category=category,
        search_query=data.query,
        limit=data.limit,
    )
    return {
        "total": total,
        "plugins": [
            {
                "id": plugin.id,
                "title": plugin.title,
                "version": plugin.version,
                "category": plugin.category.value,
                "description": (plugin.description or "")[:500],
                "dependencies": plugin.dependencies,
            }
            for plugin in plugins
        ],
    }


async def list_installed_plugins(ctx: ToolContext, _: EmptyInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    result = await ctx.db.execute(select(ManagedPlugin).where(ManagedPlugin.server_id == server.id))
    plugins = result.scalars().all()
    return {
        "plugins": [
            {
                "id": plugin.id,
                "name": plugin.display_name,
                "source_type": plugin.source_type,
                "market_plugin_id": plugin.market_plugin_id,
                "framework": plugin.framework_key,
                "version": plugin.installed_version,
            }
            for plugin in plugins
        ]
    }


async def plan_plugin_install(ctx: ToolContext, data: PluginPlanInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    from services.plugin_conflict_service import build_plugin_install_plan

    return await build_plugin_install_plan(ctx.db, server.id, data.plugin_id)


async def plan_workshop_map(ctx: ToolContext, data: WorkshopPlanInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    from services.workshop_map_service import build_workshop_map_plan

    return await build_workshop_map_plan(ctx.db, server, data.model_dump())


async def run_server_operation(ctx: ToolContext, data: ServerOperationInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    manager = SSHManager()

    async def progress(message: str) -> None:
        await ctx.emit("tool_progress", {"message": redact_sensitive_text(message, limit=2000)})

    async with maintenance_lock_service.get(
        server.id, operation=f"ai:{data.operation}", wait=False, ttl=7200
    ):
        if data.operation == "deploy":
            server.status = ServerStatus.DEPLOYING
            await ctx.db.commit()
            success, message = await manager.deploy_cs2_server(server, progress)
            server.status = ServerStatus.STOPPED if success else ServerStatus.ERROR
            if success:
                server.last_deployed = get_current_time()
        elif data.operation == "update":
            success, message = await manager.update_server(server, progress)
            if not success:
                server.status = ServerStatus.ERROR
        else:
            success, message = await manager.validate_server(server, progress)
            if not success:
                server.status = ServerStatus.ERROR
        await ctx.db.commit()
    return {"success": success, "message": redact_sensitive_text(message)}


async def control_server(ctx: ToolContext, data: ServerControlInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    manager = SSHManager()

    async def progress(message: str) -> None:
        await ctx.emit("tool_progress", {"message": redact_sensitive_text(message, limit=2000)})

    async with maintenance_lock_service.get(
        server.id, operation=f"ai:{data.action}", wait=False, ttl=900
    ):
        if data.action == "stop":
            success, message = await manager.stop_server(server)
            server.status = ServerStatus.STOPPED if success else ServerStatus.ERROR
        elif data.action == "start":
            success, message = await manager.start_server(server, progress)
            server.status = ServerStatus.RUNNING if success else ServerStatus.ERROR
        else:
            stopped, stop_message = await manager.stop_server(server)
            if not stopped:
                success, message = False, f"Restart stopped before start: {stop_message}"
            else:
                success, message = await manager.start_server(server, progress)
            server.status = ServerStatus.RUNNING if success else ServerStatus.ERROR
        await ctx.db.commit()
    return {"success": success, "message": redact_sensitive_text(message)}


async def patch_server_text_file(ctx: ToolContext, data: FilePatchInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    relative = _safe_relative_path(data.relative_path)
    allowed_extensions = (".cfg", ".txt", ".json", ".jsonc", ".ini", ".yaml", ".yml", ".toml")
    if not relative.lower().endswith(allowed_extensions):
        raise ValueError("AI edits are restricted to recognized text configuration files")
    if "\x00" in data.content:
        raise ValueError("Configuration content cannot contain null bytes")
    path = posixpath.join(server.game_directory.rstrip("/"), relative)
    manager = await _connect(server)
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
    return {
        "success": True,
        "path": relative,
        "backup_path": backup.removeprefix(server.game_directory.rstrip("/") + "/"),
        "revision": hashlib.sha256(data.content.encode()).hexdigest(),
    }


async def apply_plugin_plan(ctx: ToolContext, data: ApplyPluginPlanInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    from services.plugin_conflict_service import execute_plugin_install_plan

    return await execute_plugin_install_plan(
        ctx.db,
        server,
        ctx.user,
        data.plugin_id,
        set(data.acknowledge_warning_rule_ids),
        expected_plan_hash=data.expected_plan_hash,
        progress=lambda message, message_type: ctx.emit(
            "tool_progress", {"message": message, "message_type": message_type}
        ),
    )


async def apply_workshop_map(ctx: ToolContext, data: ApplyWorkshopPlanInput) -> dict[str, Any]:
    server = await _require_current_server(ctx)
    from services.workshop_map_service import execute_workshop_map_plan

    return await execute_workshop_map_plan(
        ctx.db,
        server,
        ctx.user,
        data.model_dump(exclude={"acknowledge_warning_rule_ids", "expected_plan_hash"}),
        set(data.acknowledge_warning_rule_ids),
        expected_plan_hash=data.expected_plan_hash,
        progress=lambda message, message_type: ctx.emit(
            "tool_progress", {"message": message, "message_type": message_type}
        ),
    )


TOOL_SPECS = (
    ToolSpec(
        "list_servers",
        "List servers the current user may access.",
        "read",
        EmptyInput,
        list_servers,
        False,
    ),
    ToolSpec(
        "inspect_server",
        "Inspect the selected CS2 deployment, process count, disk, and panel status.",
        "read",
        EmptyInput,
        inspect_server,
    ),
    ToolSpec(
        "search_server_files",
        "Find file or directory names, or bounded text matches, inside the selected game directory.",
        "read",
        FileSearchInput,
        search_server_files,
    ),
    ToolSpec(
        "read_server_text_file",
        "Read and revision a bounded text file inside the selected game directory; secrets are redacted.",
        "read",
        FileReadInput,
        read_server_text_file,
    ),
    ToolSpec(
        "tail_server_log",
        "Read a bounded tail of the selected server console.log with secret redaction.",
        "read",
        TailLogInput,
        tail_server_log,
    ),
    ToolSpec(
        "lookup_cs2_knowledge",
        f"Read one maintained CS2 operations topic. Topics: {', '.join(KNOWLEDGE_TOPICS)}.",
        "read",
        KnowledgeInput,
        lookup_cs2_knowledge,
        False,
    ),
    ToolSpec(
        "search_plugin_market",
        "Search the panel plugin market.",
        "read",
        PluginSearchInput,
        search_plugin_market,
        False,
    ),
    ToolSpec(
        "list_installed_plugins",
        "List plugins and frameworks tracked on the selected server.",
        "read",
        EmptyInput,
        list_installed_plugins,
    ),
    ToolSpec(
        "plan_plugin_install",
        "Resolve dependencies and conflicts before proposing a market plugin installation.",
        "read",
        PluginPlanInput,
        plan_plugin_install,
    ),
    ToolSpec(
        "plan_workshop_map",
        "Validate a CS2 Workshop item and plan MapChooser prerequisites and changes.",
        "read",
        WorkshopPlanInput,
        plan_workshop_map,
    ),
    ToolSpec(
        "run_server_operation",
        "Deploy, update, or validate the selected CS2 server. Requires user approval.",
        "write",
        ServerOperationInput,
        run_server_operation,
    ),
    ToolSpec(
        "control_server",
        "Start, stop, or restart the selected CS2 server. Requires user approval.",
        "write",
        ServerControlInput,
        control_server,
    ),
    ToolSpec(
        "patch_server_text_file",
        "Replace a revision-checked text configuration file after making a timestamped backup. Requires approval.",
        "write",
        FilePatchInput,
        patch_server_text_file,
    ),
    ToolSpec(
        "apply_plugin_plan",
        "Install a market plugin after fresh dependency and conflict checks. Requires approval.",
        "write",
        ApplyPluginPlanInput,
        apply_plugin_plan,
    ),
    ToolSpec(
        "apply_workshop_map",
        "Install prerequisites and add a validated CS2 Workshop map to MapChooser. Requires approval.",
        "write",
        ApplyWorkshopPlanInput,
        apply_workshop_map,
    ),
)

TOOLS_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


def tool_definitions(*, server_selected: bool) -> list[dict[str, Any]]:
    return [
        spec.api_definition() for spec in TOOL_SPECS if server_selected or not spec.requires_server
    ]


async def execute_tool(
    name: str, arguments: dict[str, Any], context: ToolContext
) -> dict[str, Any]:
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"Unknown tool: {name}")
    if spec.requires_server and context.server is None:
        raise ValueError("Select a server before using this tool")
    validated = spec.input_model.model_validate(arguments)
    return sanitize_tool_result(await spec.handler(context, validated))


def canonical_arguments(arguments: dict[str, Any]) -> tuple[str, str]:
    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return serialized, hashlib.sha256(serialized.encode()).hexdigest()


async def build_approval_summary(
    name: str, arguments: dict[str, Any], context: ToolContext
) -> dict[str, Any]:
    """Build a fresh, read-only confirmation card for a mutating tool."""
    server = await _require_current_server(context)
    base: dict[str, Any] = {
        "server": {"id": server.id, "name": server.name},
        "tool": name,
        "risk": "Changes remote server state",
    }
    if name == "patch_server_text_file":
        data = FilePatchInput.model_validate(arguments)
        relative = _safe_relative_path(data.relative_path)
        path = posixpath.join(server.game_directory.rstrip("/"), relative)
        manager = await _connect(server)
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
            success, current, error = await manager.read_file(path, server, max_size=256_000)
            if not success:
                raise RuntimeError(error)
        finally:
            await manager.disconnect()
        if hashlib.sha256(current.encode()).hexdigest() != data.expected_revision:
            raise ValueError("File changed before approval; read it again")
        diff = "".join(
            difflib.unified_diff(
                redact_sensitive_text(current).splitlines(keepends=True),
                redact_sensitive_text(data.content).splitlines(keepends=True),
                fromfile=f"{relative} (current)",
                tofile=f"{relative} (proposed)",
            )
        )
        return {
            **base,
            "target": relative,
            "backup": "A timestamped backup will be created",
            "diff": redact_sensitive_text(diff, limit=12_000),
            "expected_result": "The file is replaced only if its SHA-256 revision still matches",
        }
    if name == "apply_plugin_plan":
        data = ApplyPluginPlanInput.model_validate(arguments)
        from services.plugin_conflict_service import build_plugin_install_plan

        plan = await build_plugin_install_plan(context.db, server.id, data.plugin_id)
        if plan["plan_hash"] != data.expected_plan_hash:
            raise ValueError("Plugin plan changed before approval")
        return {
            **base,
            "target": plan["plugin"],
            "steps": plan["steps"],
            "hard_conflicts": plan["hard_conflicts"],
            "warnings": plan["warnings"],
            "expected_result": "Dependencies install in order; execution stops at the first failure",
        }
    if name == "apply_workshop_map":
        data = ApplyWorkshopPlanInput.model_validate(arguments)
        from services.workshop_map_service import build_workshop_map_plan

        plan = await build_workshop_map_plan(
            context.db,
            server,
            data.model_dump(exclude={"acknowledge_warning_rule_ids", "expected_plan_hash"}),
        )
        if plan["plan_hash"] != data.expected_plan_hash:
            raise ValueError("Workshop plan changed before approval")
        return {
            **base,
            "target": plan["workshop"],
            "steps": plan["steps"],
            "warnings": plan["warnings"],
            "expected_result": plan["download_behavior"],
        }
    if name == "run_server_operation":
        return {
            **base,
            "operation": arguments["operation"],
            "expected_result": "CS2 deployment files may be downloaded or validated",
        }
    if name == "control_server":
        return {
            **base,
            "operation": arguments["action"],
            "expected_result": "The selected server process state changes",
        }
    return {**base, "arguments": arguments}
