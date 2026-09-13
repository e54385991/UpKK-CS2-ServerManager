"""Server inspect, lifecycle, console, and startup AI tools."""

from __future__ import annotations

import logging
import posixpath
import shlex
from typing import Any

from sqlmodel import select

from modules.models import CustomCommand, ServerStatus
from modules.schemas.discord import AgentCapability
from modules.utils import get_current_time
from services.ai.tools.context import (
    ToolContext,
    tools,
)
from services.ai.tools.schemas import (
    ApplyServerStartupPlanInput,
    ChangeCurrentMapInput,
    EmptyInput,
    GameConsoleCommandInput,
    MapPoolSearchInput,
    SavedHostCommandInput,
    ServerControlInput,
    ServerOperationInput,
    ServerStartupPlanInput,
)
from services.ai_security import redact_sensitive_text, sanitize_tool_result

logger = logging.getLogger(__name__)


async def list_saved_host_commands(ctx: ToolContext, _data: EmptyInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    result = await ctx.db.execute(
        select(CustomCommand).where(
            CustomCommand.server_id == server.id,
            CustomCommand.user_id == server.user_id,
            CustomCommand.target == "host",
        )
    )
    commands = list(result.scalars().all())
    return {
        "commands": [
            {
                "id": item.id,
                "name": item.name,
                "command_hash": tools._saved_command_hash(item),
                "command_preview": redact_sensitive_text(item.commands, limit=4000),
            }
            for item in commands
        ]
    }


async def execute_saved_host_command(
    ctx: ToolContext, data: SavedHostCommandInput
) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    command = await tools.CustomCommand.get_by_id_server_and_user(
        ctx.db, data.command_id, server.id, server.user_id
    )
    if command is None or command.target != "host":
        raise ValueError("Saved host command is unavailable")
    if tools._saved_command_hash(command) != data.expected_command_hash:
        raise PermissionError("Saved host command changed after approval")
    from services.custom_command_service import execute_custom_commands

    async with tools.maintenance_lock_service.get(
        server.id, operation="ai:saved_host_command", wait=False, ttl=900
    ):
        result = await execute_custom_commands(server, command.target, command.commands)
    return sanitize_tool_result(result)


async def list_servers(ctx: ToolContext, _: EmptyInput) -> dict[str, Any]:
    servers = (
        await tools.Server.get_all(ctx.db, limit=100)
        if ctx.user.is_admin
        else await tools.Server.get_all_by_user(ctx.db, ctx.user.id, limit=100)
    )
    from services.agent_policy_service import get_effective_agent_policy

    visible = []
    for server in servers:
        if server.id is not None and (await get_effective_agent_policy(ctx.db, server.id)).enabled:
            visible.append(server)
    return {
        "servers": [
            {
                "id": server.id,
                "name": server.name,
                "status": server.status.value if server.status else "unknown",
                "game_port": server.game_port,
                "game_directory": server.game_directory,
            }
            for server in visible
        ]
    }


async def inspect_server(ctx: ToolContext, _: EmptyInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    manager = await tools._connect(server)
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


async def plan_server_startup_update(
    ctx: ToolContext, data: ServerStartupPlanInput
) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.server_startup_service import build_server_startup_plan

    return build_server_startup_plan(server, data.model_dump(exclude_unset=True))


async def run_server_operation(ctx: ToolContext, data: ServerOperationInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    manager = tools.SSHManager()
    tracking_failed = False

    async def progress(message: str) -> None:
        await ctx.emit("tool_progress", {"message": redact_sensitive_text(message, limit=2000)})

    async with tools.maintenance_lock_service.get(
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
            if success:
                server.last_update_time = get_current_time()
            else:
                server.status = ServerStatus.ERROR
        elif data.operation == "validate":
            success, message = await manager.validate_server(server, progress)
            if not success:
                server.status = ServerStatus.ERROR
        else:
            framework_key = (
                "metamod" if data.operation == "install_metamod" else "counterstrikesharp"
            )
            if framework_key == "metamod":
                success, message = await manager.install_metamod(server, progress)
                installed_frameworks = ("metamod",)
            else:
                # The panel-native CounterStrikeSharp installer checks and
                # installs Metamod itself when the prerequisite is absent.
                success, message = await manager.install_counterstrikesharp(server, progress)
                installed_frameworks = ("metamod", "counterstrikesharp")
            if success:
                from services.plugin_auto_update_service import record_framework_installation

                for installed_framework in installed_frameworks:
                    try:
                        await record_framework_installation(server, ctx.user, installed_framework)
                    except Exception as exc:
                        tracking_failed = True
                        logger.warning(
                            "Framework %s installed on server %s but tracking refresh failed: %s",
                            installed_framework,
                            server.id,
                            exc,
                        )
        await ctx.db.commit()
    result: dict[str, Any] = {
        "success": success,
        "message": redact_sensitive_text(message),
    }
    if data.operation in {"install_metamod", "install_counterstrikesharp"} and success:
        result.update(
            {
                "installation_method": "panel_native",
                "restart_required": True,
                "next_step": (
                    "Restart (or start) the server, wait for startup to complete, then locate "
                    "and read generated configuration files before patching them."
                ),
            }
        )
        if tracking_failed:
            result["tracking_warning"] = (
                "The framework was installed, but panel version tracking could not be refreshed."
            )
    return result


async def control_server(ctx: ToolContext, data: ServerControlInput) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    manager = tools.SSHManager()

    async def progress(message: str) -> None:
        await ctx.emit("tool_progress", {"message": redact_sensitive_text(message, limit=2000)})

    async with tools.maintenance_lock_service.get(
        server.id, operation=f"ai:{data.action}", wait=False, ttl=900
    ):
        tools.apply_user_lifecycle_intent(server, data.action)
        await ctx.db.commit()
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


async def send_game_console_command(
    ctx: ToolContext, data: GameConsoleCommandInput
) -> dict[str, Any]:
    """Send one confirmed literal command to the detached CS2 process, never host Shell."""

    server = await tools._require_current_server(ctx)
    await tools._require_active_user(ctx)
    from services.custom_command_service import execute_custom_commands

    async with tools.maintenance_lock_service.get(
        server.id, operation="game_console_command", wait=False, ttl=120
    ):
        result = await execute_custom_commands(
            server,
            "game_process",
            data.command,
            capture_game_output=True,
        )
    return sanitize_tool_result(result)


async def search_map_pool(ctx: ToolContext, data: MapPoolSearchInput) -> dict[str, Any]:
    """Search the selected server MapChooser pool by name fragment or Workshop ID."""
    server = await tools._require_current_server(ctx)
    from services.change_map_service import load_map_matches, workshop_id_fallback

    matches = await load_map_matches(server, data.query)
    if not matches:
        fallback = workshop_id_fallback(data.query)
        matches = [fallback] if fallback is not None else []
    return {
        "query": data.query.strip(),
        "count": len(matches),
        "unique": len(matches) == 1,
        "matches": [item.to_public_dict() for item in matches[:25]],
    }


async def change_current_map(ctx: ToolContext, data: ChangeCurrentMapInput) -> dict[str, Any]:
    """Resolve one map and send host_workshop_map or map to the running game console."""
    server = await tools._require_current_server(ctx)
    if ctx.enforce_agent_policy:
        from services.agent_policy_service import AgentCapabilityDenied, get_effective_agent_policy

        policy = await get_effective_agent_policy(ctx.db, server.id)
        allowed = set(policy.capabilities)
        if (
            AgentCapability.CHANGE_CURRENT_MAP not in allowed
            and AgentCapability.SEND_GAME_CONSOLE_COMMANDS not in allowed
        ):
            raise AgentCapabilityDenied("AI capability is disabled: change_current_map")
    from services.change_map_service import load_map_pool, resolve_unique_map

    candidate = resolve_unique_map(await load_map_pool(server), data.query)
    result = await send_game_console_command(
        ctx, GameConsoleCommandInput(command=candidate.command)
    )
    return {
        **result,
        "map": candidate.to_public_dict(),
        "command": candidate.command,
    }


async def apply_server_startup_update(
    ctx: ToolContext, data: ApplyServerStartupPlanInput
) -> dict[str, Any]:
    server = await tools._require_current_server(ctx)
    from services.server_startup_service import execute_server_startup_plan

    return await execute_server_startup_plan(
        ctx.db,
        ctx.user,
        server.id,
        data.model_dump(exclude={"expected_plan_hash"}, exclude_unset=True),
        data.expected_plan_hash,
        progress=lambda message, message_type, metadata=None: ctx.emit(
            "tool_progress",
            {"message": message, "message_type": message_type, **(metadata or {})},
        ),
        lock_operation="ai:server_startup_update",
    )
