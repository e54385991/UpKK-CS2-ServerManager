"""Read-only approval cards for mutating AI tools."""

from __future__ import annotations

import difflib
import hashlib
import posixpath
from typing import Any

from modules.schemas.discord import AgentCapability
from services.ai.tools.context import (
    ToolContext,
    _safe_relative_path,
    canonical_arguments,
    tools,
)
from services.ai.tools.schemas import (
    ApplyManagedPluginUpgradeInput,
    ApplyPluginPlanInput,
    ApplyServerStartupPlanInput,
    ApplyWorkshopPlanInput,
    ChangeCurrentMapInput,
    DiagnosticExecuteInput,
    DiagnosticRunInput,
    FilePatchInput,
    GameConsoleCommandInput,
    GitHubApplyInput,
    SavedHostCommandInput,
)
from services.ai_security import redact_sensitive_text


async def build_approval_summary(  # noqa: C901
    name: str, arguments: dict[str, Any], context: ToolContext
) -> dict[str, Any]:
    """Build a fresh, read-only confirmation card for a mutating tool."""
    server = await tools._require_current_server(context)
    base: dict[str, Any] = {
        "server": {
            "id": server.id,
            "name": server.name,
            "host": server.host,
            "ssh_port": server.ssh_port,
            "game_port": server.game_port,
            "game_directory": server.game_directory,
        },
        "tool": name,
        "risk": "Changes remote server state",
    }
    if name == "patch_server_text_file":
        data = FilePatchInput.model_validate(arguments)
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

        plan = await build_plugin_install_plan(context.db, server.id, data.plugin_id, server=server)
        if plan["plan_hash"] != data.expected_plan_hash:
            raise ValueError("Plugin plan changed before approval")
        plugins = await tools.MarketPlugin.get_by_ids(context.db, plan["installation_order"])
        from services.plugin_conflict_service import _panel_framework_key

        if any(
            _panel_framework_key(plugin) is not None
            and plugin.id not in set(plan["already_installed"])
            for plugin in plugins
        ):
            from services.agent_policy_service import require_agent_capabilities

            await require_agent_capabilities(
                context.db, server.id, frozenset({AgentCapability.MANAGE_FRAMEWORKS})
            )
        from services.linux_runtime_service import detect_linux_runtime_profile

        linux_runtime_profile = await detect_linux_runtime_profile(server)
        release_selections = await tools._market_release_selection_preview(
            context.db,
            server,
            context.user,
            plan,
            linux_runtime_profile,
        )
        return {
            **base,
            "target": plan["plugin"],
            "steps": plan["steps"],
            "hard_conflicts": plan["hard_conflicts"],
            "warnings": plan["warnings"],
            "linux_runtime_profile": linux_runtime_profile,
            "release_selections": release_selections,
            "post_install": (
                "A separate server restart/start is required before generated configs are inspected"
            ),
            "expected_result": (
                "Dependencies install in order; execution stops at the first failure and reports "
                "restart_required after any successful installation"
            ),
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
    if name == "execute_plugin_crash_isolation":
        data = DiagnosticExecuteInput.model_validate(arguments)
        from services.plugin_diagnostic_service import build_diagnostic_plan

        plan = await build_diagnostic_plan(context.db, context.user, server.id, data.scope)
        if plan["plan_hash"] != data.expected_plan_hash:
            raise ValueError("Diagnostic plan changed before approval")
        return {
            **base,
            "scope": data.scope,
            "candidate_groups": plan["candidates"],
            "maximum_starts": plan["health_policy"]["max_start_attempts"],
            "maximum_duration_seconds": plan["health_policy"]["max_duration_seconds"],
            "steps": [
                "stop before every isolation change",
                "verify baseline without third-party plugins",
                "narrow groups and confirm final candidates",
                "restore unrelated plugins and verify final health",
            ],
            "expected_result": "Only reproduced crash candidates remain quarantined",
        }
    if name == "restore_plugin_quarantine":
        data = DiagnosticRunInput.model_validate(arguments)
        from services.plugin_diagnostic_service import get_diagnostic_run

        diagnostic = await get_diagnostic_run(
            context.db, context.user, server.id, data.diagnostic_id
        )
        return {
            **base,
            "diagnostic_id": data.diagnostic_id,
            "quarantine": diagnostic["quarantine"],
            "expected_result": "All items in the immutable diagnostic manifest are restored",
        }
    if name == "apply_github_plugin_install":
        data = GitHubApplyInput.model_validate(arguments)
        from modules.schemas.plugins import GitHubPluginInstallPlanRequest
        from services.github_plugin_plan_service import build_github_install_plan

        request = GitHubPluginInstallPlanRequest.model_validate(
            data.model_dump(
                exclude={
                    "expected_plan_hash",
                    "acknowledge_warning_rule_ids",
                    "acknowledge_unknown_compatibility",
                }
            )
        )
        plan = await build_github_install_plan(context.db, context.user, server.id, request)
        if plan["plan_hash"] != data.expected_plan_hash:
            raise ValueError("GitHub installation plan changed before approval")
        return {
            **base,
            "repository": plan["repo_url"],
            "release": plan["release_tag"],
            "asset": plan["asset"],
            "archive_sha256": plan["archive_sha256"],
            "mapping": plan["mapping"],
            "config_policy": plan["config_policy"],
            "warnings": plan["warnings"],
            "hard_conflicts": plan["hard_conflicts"],
            "conflict_warnings": plan["conflict_warnings"],
            "compatibility_unknown": plan["compatibility_unknown"],
            "post_install": (
                "A separate server restart/start is required before generated configs are inspected"
            ),
            "expected_result": (
                "The verified release is staged, backed up, installed, recorded, and reports "
                "restart_required"
            ),
        }
    if name == "run_server_operation":
        operation = arguments["operation"]
        if operation in {"install_metamod", "install_counterstrikesharp"}:
            return {
                **base,
                "operation": operation,
                "installation_method": "panel_native",
                "steps": [
                    "install with the panel framework installer",
                    "record framework tracking metadata",
                    "require a separate restart before generated config inspection",
                ],
                "expected_result": (
                    "The framework is installed through the panel and reports restart_required"
                ),
            }
        return {
            **base,
            "operation": operation,
            "expected_result": "CS2 deployment files may be downloaded or validated",
        }
    if name == "control_server":
        return {
            **base,
            "operation": arguments["action"],
            "expected_result": "The selected server process state changes",
        }
    if name == "send_game_console_command":
        data = GameConsoleCommandInput.model_validate(arguments)
        return {
            **base,
            "target": "Running CS2 game-process console (not host Shell)",
            "command": redact_sensitive_text(data.command, limit=500),
            "command_hash": hashlib.sha256(data.command.encode()).hexdigest(),
            "steps": [
                "acquire the server maintenance lock",
                "locate the exact configured or legacy screen/tmux session",
                "snapshot the current bounded game-console output",
                "send the approved command as literal input followed by Enter",
                "poll briefly and return newly observed console output to the agent",
            ],
            "expected_result": (
                "The command is delivered once to the running game process and newly observed "
                "console output is returned; it is not interpreted as host Shell output"
            ),
        }
    if name == "change_current_map":
        data = ChangeCurrentMapInput.model_validate(arguments)
        from services.change_map_service import load_map_pool, resolve_unique_map

        candidate = resolve_unique_map(await load_map_pool(server), data.query)
        command = candidate.command
        return {
            **base,
            "target": "Running CS2 game-process console (not host Shell)",
            "map": candidate.to_public_dict(),
            "command": redact_sensitive_text(command, limit=500),
            "command_hash": hashlib.sha256(command.encode()).hexdigest(),
            "steps": [
                "resolve one map from the MapChooser pool by name or Workshop ID",
                "acquire the server maintenance lock",
                "send host_workshop_map {id} for Workshop maps, or map {name} for official maps",
            ],
            "expected_result": "The running CS2 process changes to the resolved map",
        }
    if name == "apply_server_startup_update":
        data = ApplyServerStartupPlanInput.model_validate(arguments)
        from services.server_startup_service import build_server_startup_plan

        plan = build_server_startup_plan(
            server,
            data.model_dump(exclude={"expected_plan_hash"}, exclude_unset=True),
        )
        if plan["plan_hash"] != data.expected_plan_hash:
            raise ValueError("Startup configuration changed before approval")
        if plan["blocked"]:
            raise ValueError("; ".join(plan["blocking_reasons"]))
        return {
            **base,
            "target": "CS2 startup settings",
            "configuration_revision": plan["configuration_revision"],
            "changes": plan["changes"],
            "steps": plan["steps"],
            "partial_failure_policy": plan["partial_failure_policy"],
            "expected_result": (
                "Settings are saved, the server is restarted, and process/A2S state is verified"
            ),
        }
    if name == "execute_saved_host_command":
        data = SavedHostCommandInput.model_validate(arguments)
        command = await tools.CustomCommand.get_by_id_server_and_user(
            context.db, data.command_id, server.id, server.user_id
        )
        if command is None or command.target != "host":
            raise ValueError("Saved host command is unavailable")
        if tools._saved_command_hash(command) != data.expected_command_hash:
            raise ValueError("Saved host command changed before approval")
        return {
            **base,
            "command_id": command.id,
            "command_name": command.name,
            "command_hash": data.expected_command_hash,
            "full_command": redact_sensitive_text(command.commands, limit=12_000),
            "expected_result": "The exact saved host command revision is executed once",
        }
    if name == "apply_managed_plugin_upgrade":
        data = ApplyManagedPluginUpgradeInput.model_validate(arguments)
        from services.plugin_auto_update_service import plugin_auto_update_service

        plan = await plugin_auto_update_service.build_plugin_upgrade_plan(server.id, data.plugin_id)
        _, plan_hash = canonical_arguments(plan)
        if plan_hash != data.expected_plan_hash:
            raise ValueError("Managed plugin upgrade plan changed before approval")
        return {
            **base,
            "upgrade_plan": plan,
            "expected_plan_hash": plan_hash,
            "expected_result": "Only the selected managed plugin/framework is upgraded",
        }
    return {**base, "arguments": arguments}
